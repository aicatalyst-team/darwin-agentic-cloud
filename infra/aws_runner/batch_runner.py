"""
darwin-batch-runner container (Python).

Deployed as a container image to AWS Batch via ECR. One image per
language: darwin-batch-runner-python-{region}. The substrate adapter
(darwin.agenticcloud.substrate.aws_batch.BatchSubstrate) submits jobs
that reference this image's job definition.

This file is the container entrypoint. It is NOT imported by the rest
of the codebase — it is packaged into a container image and shipped
to ECR. Tests for the schema it consumes/produces live alongside the
substrate adapter (tests/substrate/test_aws_batch.py).

Schema contract (shared with the substrate adapter):
    darwin.agenticcloud.substrate.aws_batch_event
        → EVENT_SCHEMA_URI, BatchRunnerEvent, BatchRunnerResponse

Execution model differs from Lambda in three ways:

1. Event arrives via the DARWIN_BATCH_EVENT_B64 environment variable
   (base64-encoded JSON), not as a function argument. Batch has no
   structured event channel.

2. Response is written to S3 at s3://{DARWIN_BATCH_RESULT_BUCKET}/
   {request_id}.json, not returned as a function result. Batch has
   no return-value channel.

3. Exit code is the process exit code of this script. Batch surfaces
   it as containerExitCode on the job, which the substrate adapter
   reads via describe_jobs.

Steps:
    1. Read DARWIN_BATCH_EVENT_B64 from env, decode + validate.
    2. Materialize workload code into /tmp/workload.py.
    3. subprocess.run() python3 against the file with declared timeout.
    4. Capture stdout, stderr, exit_code, wall time.
    5. Compute SHA-256 hashes of stdout and stderr.
    6. Build BatchRunnerResponse dict, write to S3.
    7. Print response to stdout (CloudWatch picks it up for audit).
    8. Exit with the workload's exit code (or 1 on runner error).

Failure modes:
    - Event validation fails → exit 2, write error response to S3.
    - Workload exits non-zero → status="error", exit_code populated.
    - Workload exceeds timeout → status="timeout", exit_code=None.
    - Spot reclamation → SIGTERM from Batch; handler writes
      status="spot-reclaimed" and exits.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from typing import Any

import boto3

# ============================================================================
# Schema contract (kept in lockstep with
# darwin.agenticcloud.substrate.aws_batch_event)
# ============================================================================
#
# We do NOT import the substrate module here — the container image
# shouldn't carry the entire darwin package. Instead we duplicate the
# small set of constants and validators we need. Drift is caught by
# tests/substrate/test_aws_batch.py which uses the canonical module.

EVENT_SCHEMA_URI = "darwin.cloud/event/aws-batch-runner/v1"
RESPONSE_SCHEMA_URI = "darwin.cloud/event/aws-batch-runner/v1"
SUPPORTED_LANGUAGES = {"python"}
SUPPORTED_INSTANCE_TYPES = {"m5.xlarge"}
MAX_CODE_BYTES = 1024 * 1024
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 16384
MAX_VCPUS = 4
MIN_TIMEOUT_SEC = 10
MAX_TIMEOUT_SEC = 86400

LANGUAGE_INTERPRETER = {
    "python": ["python3", "/tmp/workload.py"],
}
LANGUAGE_FILENAME = {
    "python": "workload.py",
}


class BatchRunnerEventError(Exception):
    """Raised when the incoming event payload is malformed."""


# ============================================================================
# Event validation
# ============================================================================


def _validate_event(event: dict[str, Any]) -> None:
    """Replica of aws_batch_event.validate_event_dict."""
    if not isinstance(event, dict):
        raise BatchRunnerEventError("event must be a JSON object")

    required = {
        "schema",
        "request_id",
        "workload_id",
        "language",
        "code",
        "timeout_sec",
        "memory_mb",
        "vcpus",
        "instance_type",
    }
    missing = required - event.keys()
    if missing:
        raise BatchRunnerEventError(f"event missing fields: {sorted(missing)}")

    if event["schema"] != EVENT_SCHEMA_URI:
        raise BatchRunnerEventError(
            f"event schema mismatch: got {event['schema']!r}, expected {EVENT_SCHEMA_URI!r}"
        )
    if event["language"] not in SUPPORTED_LANGUAGES:
        raise BatchRunnerEventError(f"event language not supported: {event['language']!r}")
    if event["instance_type"] not in SUPPORTED_INSTANCE_TYPES:
        raise BatchRunnerEventError(
            f"event instance_type not supported: {event['instance_type']!r}"
        )

    code = event["code"]
    if not isinstance(code, str) or len(code) == 0:
        raise BatchRunnerEventError("event.code must be a non-empty string")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise BatchRunnerEventError(f"event.code exceeds {MAX_CODE_BYTES} bytes")

    timeout = event["timeout_sec"]
    if not isinstance(timeout, int) or timeout < MIN_TIMEOUT_SEC or timeout > MAX_TIMEOUT_SEC:
        raise BatchRunnerEventError(
            f"event.timeout_sec must be {MIN_TIMEOUT_SEC}..{MAX_TIMEOUT_SEC}, got {timeout!r}"
        )

    memory = event["memory_mb"]
    if not isinstance(memory, int) or memory < MIN_MEMORY_MB or memory > MAX_MEMORY_MB:
        raise BatchRunnerEventError(
            f"event.memory_mb must be {MIN_MEMORY_MB}..{MAX_MEMORY_MB}, got {memory!r}"
        )

    vcpus = event["vcpus"]
    if not isinstance(vcpus, int) or vcpus < 1 or vcpus > MAX_VCPUS:
        raise BatchRunnerEventError(f"event.vcpus must be 1..{MAX_VCPUS}, got {vcpus!r}")


# ============================================================================
# Execution
# ============================================================================


def _execute_workload(
    *,
    language: str,
    code: str,
    timeout_sec: int,
) -> dict[str, Any]:
    """Run the workload and return raw execution metrics."""
    filename = LANGUAGE_FILENAME[language]
    file_path = f"/tmp/{filename}"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    interpreter = LANGUAGE_INTERPRETER[language]
    started_at = time.time()
    try:
        proc = subprocess.run(
            interpreter,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        ended_at = time.time()
        status = "ok" if proc.returncode == 0 else "error"
        return {
            "status": status,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "started_at": started_at,
            "ended_at": ended_at,
            "wall_time_sec": ended_at - started_at,
        }
    except subprocess.TimeoutExpired as exc:
        ended_at = time.time()
        return {
            "status": "timeout",
            "stdout": (exc.stdout or b"").decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or ""),
            "stderr": (exc.stderr or b"").decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or ""),
            "exit_code": None,
            "started_at": started_at,
            "ended_at": ended_at,
            "wall_time_sec": ended_at - started_at,
        }


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _build_response(
    *,
    event: dict[str, Any],
    execution: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    """Build a BatchRunnerResponse dict from event + execution metrics."""
    return {
        "schema": RESPONSE_SCHEMA_URI,
        "request_id": event["request_id"],
        "workload_id": event["workload_id"],
        "status": execution["status"],
        "stdout": execution.get("stdout", ""),
        "stderr": execution.get("stderr", ""),
        "exit_code": execution.get("exit_code"),
        "started_at": execution["started_at"],
        "ended_at": execution["ended_at"],
        "wall_time_sec": execution["wall_time_sec"],
        "output_hash": _sha256(execution.get("stdout", "")),
        "stderr_hash": _sha256(execution.get("stderr", "")),
        **({"error": error} if error else {}),
    }


def _write_response_to_s3(response: dict[str, Any]) -> None:
    """Write the response JSON to S3 at the bucket configured in env."""
    bucket = os.environ.get("DARWIN_BATCH_RESULT_BUCKET")
    if not bucket:
        # No bucket configured: only emit to stdout. The substrate
        # adapter is responsible for handling missing-result-bucket
        # configuration; this is not a hard failure here.
        return
    s3 = boto3.client("s3")
    key = f"{response['request_id']}.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(response).encode("utf-8"),
        ContentType="application/json",
    )


# ============================================================================
# Spot reclamation handler
# ============================================================================

_RECLAMATION_FLAG = {"reclaimed": False}


def _on_sigterm(signum: int, frame: Any) -> None:
    """Set the reclamation flag and exit gracefully.

    Batch sends SIGTERM with ~120 seconds notice when EC2 Spot reclaims
    the underlying instance. We mark the run as spot-reclaimed and
    return so the response can be emitted before the kernel kills us.
    """
    _RECLAMATION_FLAG["reclaimed"] = True


# ============================================================================
# Entry point
# ============================================================================


def main() -> int:
    """Container entrypoint. Returns process exit code."""
    signal.signal(signal.SIGTERM, _on_sigterm)

    raw_event_b64 = os.environ.get("DARWIN_BATCH_EVENT_B64")
    if not raw_event_b64:
        sys.stderr.write("DARWIN_BATCH_EVENT_B64 environment variable is required\n")
        return 2

    try:
        raw_event_bytes = base64.b64decode(raw_event_b64)
        event = json.loads(raw_event_bytes.decode("utf-8"))
        _validate_event(event)
    except (ValueError, BatchRunnerEventError) as exc:
        sys.stderr.write(f"event validation failed: {exc}\n")
        error_response = {
            "schema": RESPONSE_SCHEMA_URI,
            "request_id": "unknown",
            "workload_id": "unknown",
            "status": "error",
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "started_at": time.time(),
            "ended_at": time.time(),
            "wall_time_sec": 0.0,
            "output_hash": _sha256(""),
            "stderr_hash": _sha256(str(exc)),
            "error": f"event validation failed: {exc}",
        }
        try:
            _write_response_to_s3(error_response)
        except Exception:
            sys.stderr.write(traceback.format_exc())
        print(json.dumps(error_response))
        return 2

    try:
        execution = _execute_workload(
            language=event["language"],
            code=event["code"],
            timeout_sec=int(event["timeout_sec"]),
        )
    except Exception as exc:
        sys.stderr.write(traceback.format_exc())
        execution = {
            "status": "error",
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "started_at": time.time(),
            "ended_at": time.time(),
            "wall_time_sec": 0.0,
        }

    if _RECLAMATION_FLAG["reclaimed"]:
        execution["status"] = "spot-reclaimed"

    response = _build_response(event=event, execution=execution)
    try:
        _write_response_to_s3(response)
    except Exception:
        sys.stderr.write(traceback.format_exc())

    print(json.dumps(response))

    exit_code = execution.get("exit_code")
    if exit_code is None:
        return 1
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
