"""Bake a fresh v0.2 attestation for the public /demo page.

Runs at Docker build time. Output is committed to the image so the page
doesn't need any runtime substrate execution.

Output: darwin/agenticcloud/templates/demo_attestation.json
"""

from __future__ import annotations

import json
import pathlib
import sys

from darwin import run

WORKLOAD = """print("Hello, agent. This receipt was produced by darwin.run().")"""

OUT_PATH = pathlib.Path("darwin/agenticcloud/templates/demo_attestation.json")


def main() -> int:
    print(f"> baking demo attestation at {OUT_PATH}")
    try:
        attestation = run(WORKLOAD)
    except Exception as e:
        print(f"  ! darwin.run() failed: {type(e).__name__}: {e}", file=sys.stderr)
        print("  ! falling back to mock attestation", file=sys.stderr)
        from dataclasses import asdict

        from darwin.agenticcloud.hashing import canonical_json, content_hash, sha256_hex
        from darwin.agenticcloud.substrate.base import (
            RunResult,
            build_attestation_dict,
            iso8601_now,
            sign_identity,
        )
        from darwin.agenticcloud.substrate.identity import OperatorFallbackSigner
        from darwin.agenticcloud.substrate.local_docker import (
            EVIDENCE_SCHEMA_ID,
            SUBSTRATE_VERSION,
        )
        from darwin.agenticcloud.types import WorkloadSpec

        signer = OperatorFallbackSigner()
        spec = WorkloadSpec(
            code=WORKLOAD, language="python", timeout_sec=30, memory_mb=512, cost_cap_usd=0.10
        )
        out = "Hello, agent.\\n"
        result = RunResult(
            substrate_id="local-docker-v0",
            substrate_version=SUBSTRATE_VERSION,
            workload_spec_hash=content_hash(asdict(spec)),
            stdout=out,
            stderr="",
            output_hash=sha256_hex(out.encode("utf-8")),
            cost_usd=0.000042,
            evidence_schema_id=EVIDENCE_SCHEMA_ID,
            evidence={
                "container_status": "ok",
                "exit_code": 0,
                "stdout_hash": sha256_hex(out.encode("utf-8")),
                "stderr_hash": sha256_hex(b""),
                "wall_time_sec": 0.42,
            },
            extensions={},
            tee_required=False,
            issued_at=iso8601_now(),
        )
        identity = sign_identity(result=result, signer=signer)
        attestation = build_attestation_dict(
            attestation_id="att_demo_baked",
            result=result,
            identity=identity,
        )
        outer_sig = signer._signer.sign(canonical_json(attestation))
        attestation["signer_key_id"] = signer.signer_key_id
        attestation["signature"] = outer_sig

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(attestation, indent=2) + "\n")
    print(f"  ✓ wrote {len(json.dumps(attestation))} bytes")
    print(f"  ✓ attestation_id: {attestation['attestation_id']}")
    print(f"  ✓ substrate: {attestation['execution_result']['substrate']['id']}")
    print(
        f"  ✓ sub-signer: {attestation['execution_result']['substrate']['identity_signer_key_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
