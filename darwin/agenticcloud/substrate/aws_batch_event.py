"""
darwin.agenticcloud.substrate.aws_batch_event
==============================================

Shared contract for the event payload sent to the darwin-batch-runner
container in AWS Batch.

This module is imported by TWO different deployment artifacts:

1. ``darwin.agenticcloud.substrate.aws_batch.BatchSubstrate`` (this repo,
   client side) constructs the event, base64-encodes it, and passes it to
   the Batch job as the ``DARWIN_BATCH_EVENT_B64`` environment variable.

2. ``darwin-batch-runner`` container (``infra/aws_runner/batch_runner.py``,
   deployed artifact) reads the env var, decodes, validates, and runs the
   workload.

Keeping the schema in ONE file that BOTH import is intentional. If the
contract drifts, attestations silently break and we have no test signal
until production. By co-locating the schema, any version bump shows up
in code review on both sides at once. Same discipline as
``aws_lambda_event.py``.

Schema URI: ``darwin.cloud/event/aws-batch-runner/v1``

Key differences from the Lambda runner event:

- No memory cap of 10240 MB. Batch on EC2 supports up to the instance
  type ceiling (m5.xlarge = 16384 MB; larger families higher).
- No 900s timeout cap. Batch has no execution time limit at the platform
  level; we cap at 24h to bound runaway-job risk.
- ``instance_type`` is part of the event because EC2 Spot pricing varies
  by instance type and the spot quote is captured at submit time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ============================================================================
# Constants
# ============================================================================

#: Schema URI for the batch-runner-event payload. Bumped when the event
#: shape changes in a way that breaks runner compatibility.
EVENT_SCHEMA_URI: str = "darwin.cloud/event/aws-batch-runner/v1"

#: Supported workload languages. Each maps to a runner image
#: (darwin-batch-runner-python). Other languages added by building
#: additional runner images.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"python"})

#: Maximum workload code size accepted by the runner. Batch passes the
#: event via an environment variable, which on Linux is bounded by ARG_MAX
#: (typically ~2 MB). Cap at 1 MB raw to leave generous headroom for
#: base64 expansion and other env vars.
MAX_CODE_BYTES: int = 1024 * 1024

#: Minimum memory in MB. EC2 m5 family minimum is 8 GB; we accept 512 MB
#: at the spec level and let Batch handle instance selection.
MIN_MEMORY_MB: int = 512

#: Maximum memory in MB at the substrate level. m5.xlarge ceiling is
#: 16384. Larger workloads must explicitly request a larger family in a
#: future substrate revision.
MAX_MEMORY_MB: int = 16384

#: Maximum vCPU count at the substrate level. m5.xlarge ceiling is 4.
MAX_VCPUS: int = 4

#: Maximum timeout. 24h hard ceiling to bound spend exposure on runaway
#: jobs. Batch's platform limit is much higher; this is Darwin policy,
#: not AWS policy.
MAX_TIMEOUT_SEC: int = 86400

#: Minimum timeout. EC2 Spot has a 60-second billing minimum, so jobs
#: shorter than that are charged the minimum anyway. Reject below 10s
#: at the substrate level — anything that short belongs on Lambda.
MIN_TIMEOUT_SEC: int = 10

#: Default instance type. Substrate hardcodes m5.xlarge for v3.0.0;
#: future revisions can parametrize.
DEFAULT_INSTANCE_TYPE: str = "m5.xlarge"

#: Supported instance types for v3.0.0. m5.xlarge is the only one wired
#: through pricing + tests. Other families added by extending the
#: substrate adapter and re-running fixture capture.
SUPPORTED_INSTANCE_TYPES: frozenset[str] = frozenset({"m5.xlarge"})


# ============================================================================
# Errors
# ============================================================================


class BatchRunnerEventError(Exception):
    """Raised when a batch-runner-event payload is malformed."""


# ============================================================================
# Schema
# ============================================================================


@dataclass(frozen=True)
class BatchRunnerEvent:
    """The event payload passed to the darwin-batch-runner container.

    Always serialized via ``to_dict()`` / parsed via ``from_dict()`` so
    both sides go through validation. Never directly serialized to JSON
    by the caller.
    """

    schema: str
    request_id: str
    workload_id: str
    language: str
    code: str
    timeout_sec: int
    memory_mb: int
    vcpus: int
    instance_type: str
    inputs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for transmission as the Batch job env payload."""
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "workload_id": self.workload_id,
            "language": self.language,
            "code": self.code,
            "timeout_sec": self.timeout_sec,
            "memory_mb": self.memory_mb,
            "vcpus": self.vcpus,
            "instance_type": self.instance_type,
            "inputs": self.inputs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchRunnerEvent:
        """Parse and validate an incoming event payload (runner side)."""
        validate_event_dict(data)
        return cls(
            schema=data["schema"],
            request_id=data["request_id"],
            workload_id=data["workload_id"],
            language=data["language"],
            code=data["code"],
            timeout_sec=int(data["timeout_sec"]),
            memory_mb=int(data["memory_mb"]),
            vcpus=int(data["vcpus"]),
            instance_type=data["instance_type"],
            inputs=dict(data.get("inputs", {})),
        )


@dataclass(frozen=True)
class BatchRunnerResponse:
    """The response the darwin-batch-runner emits to S3 + stdout.

    Mirrors the v0.2 evidence shape so the substrate can pass it through
    to the attestation builder with minimal translation. Same field set
    as ``RunnerResponse`` (Lambda) plus Batch-specific evidence
    (``job_id``, ``log_stream``) carried in the ``BatchSubstrate``
    adapter, not in this response.
    """

    schema: str
    request_id: str
    workload_id: str
    status: str  # "ok" | "error" | "timeout" | "oom" | "spot-reclaimed"
    stdout: str
    stderr: str
    exit_code: int | None
    started_at: float
    ended_at: float
    wall_time_sec: float
    output_hash: str
    stderr_hash: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema": self.schema,
            "request_id": self.request_id,
            "workload_id": self.workload_id,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_time_sec": self.wall_time_sec,
            "output_hash": self.output_hash,
            "stderr_hash": self.stderr_hash,
        }
        if self.error is not None:
            d["error"] = self.error
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchRunnerResponse:
        validate_response_dict(data)
        return cls(
            schema=data["schema"],
            request_id=data["request_id"],
            workload_id=data["workload_id"],
            status=data["status"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exit_code"),
            started_at=float(data["started_at"]),
            ended_at=float(data["ended_at"]),
            wall_time_sec=float(data["wall_time_sec"]),
            output_hash=data["output_hash"],
            stderr_hash=data["stderr_hash"],
            error=data.get("error"),
        )


# ============================================================================
# Validators
# ============================================================================

_REQUIRED_EVENT_FIELDS: frozenset[str] = frozenset(
    {
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
)

_REQUIRED_RESPONSE_FIELDS: frozenset[str] = frozenset(
    {
        "schema",
        "request_id",
        "workload_id",
        "status",
        "started_at",
        "ended_at",
        "wall_time_sec",
        "output_hash",
        "stderr_hash",
    }
)

_VALID_RESPONSE_STATUSES: frozenset[str] = frozenset(
    {"ok", "error", "timeout", "oom", "spot-reclaimed"}
)


def validate_event_dict(data: dict[str, Any]) -> None:
    """Validate a runner-event payload. Raises BatchRunnerEventError."""
    if not isinstance(data, dict):
        raise BatchRunnerEventError(f"event must be dict, got {type(data).__name__}")

    missing = _REQUIRED_EVENT_FIELDS - set(data.keys())
    if missing:
        raise BatchRunnerEventError(f"event missing required fields: {sorted(missing)}")

    if data["schema"] != EVENT_SCHEMA_URI:
        raise BatchRunnerEventError(
            f"event schema mismatch: got {data['schema']!r}, expected {EVENT_SCHEMA_URI!r}"
        )

    if data["language"] not in SUPPORTED_LANGUAGES:
        raise BatchRunnerEventError(
            f"unsupported language: {data['language']!r}; supported: {sorted(SUPPORTED_LANGUAGES)}"
        )

    if data["instance_type"] not in SUPPORTED_INSTANCE_TYPES:
        raise BatchRunnerEventError(
            f"unsupported instance_type: {data['instance_type']!r}; "
            f"supported: {sorted(SUPPORTED_INSTANCE_TYPES)}"
        )

    code = data["code"]
    if not isinstance(code, str):
        raise BatchRunnerEventError("code must be a string")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise BatchRunnerEventError(f"code exceeds MAX_CODE_BYTES ({MAX_CODE_BYTES})")

    timeout = int(data["timeout_sec"])
    if timeout < MIN_TIMEOUT_SEC or timeout > MAX_TIMEOUT_SEC:
        raise BatchRunnerEventError(
            f"timeout_sec {timeout} out of range [{MIN_TIMEOUT_SEC}, {MAX_TIMEOUT_SEC}]"
        )

    memory = int(data["memory_mb"])
    if memory < MIN_MEMORY_MB or memory > MAX_MEMORY_MB:
        raise BatchRunnerEventError(
            f"memory_mb {memory} out of range [{MIN_MEMORY_MB}, {MAX_MEMORY_MB}]"
        )

    vcpus = int(data["vcpus"])
    if vcpus < 1 or vcpus > MAX_VCPUS:
        raise BatchRunnerEventError(f"vcpus {vcpus} out of range [1, {MAX_VCPUS}]")


def validate_response_dict(data: dict[str, Any]) -> None:
    """Validate a runner-response payload. Raises BatchRunnerEventError."""
    if not isinstance(data, dict):
        raise BatchRunnerEventError(f"response must be dict, got {type(data).__name__}")

    missing = _REQUIRED_RESPONSE_FIELDS - set(data.keys())
    if missing:
        raise BatchRunnerEventError(f"response missing required fields: {sorted(missing)}")

    if data["status"] not in _VALID_RESPONSE_STATUSES:
        raise BatchRunnerEventError(
            f"invalid status: {data['status']!r}; valid: {sorted(_VALID_RESPONSE_STATUSES)}"
        )
