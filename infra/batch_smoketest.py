r"""Real AWS Batch (EC2 Spot) smoke test. Not part of the unit suite.
Run manually with:

    AWS_PROFILE=darwin uv run python infra/batch_smoketest.py

Will submit a real Batch job, provision a real m5.xlarge Spot instance
(cold start ~3 minutes), and incur real cost (~\$0.001-0.002).
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace as dataclass_replace

from darwin.agenticcloud.substrate.aws_batch import BatchSubstrate
from darwin.agenticcloud.substrate.base import (
    build_attestation_dict,
    iso8601_now,
    sign_identity,
)
from darwin.agenticcloud.substrate.identity import OperatorFallbackSigner
from darwin.agenticcloud.types import WorkloadSpec

ACCOUNT_ID = "529088294890"
REGION = "us-east-1"
RESULT_BUCKET = f"darwin-batch-results-{ACCOUNT_ID}-{REGION}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print raw attestation JSON instead of the branded panel.",
    )
    args = parser.parse_args()
    as_json = args.as_json

    os.environ.setdefault("AWS_PROFILE", "darwin")

    signer = OperatorFallbackSigner()
    sub = BatchSubstrate(
        region=REGION,
        result_bucket=RESULT_BUCKET,
        identity_signer=signer,
        poll_interval_sec=10.0,
        run_timeout_sec=20 * 60,
    )

    ws = WorkloadSpec(
        code="print('Hello from real AWS Batch.')",
        language="python",
        timeout_sec=30,
        memory_mb=512,
        cost_cap_usd=1.0,
    )

    print("substrate:", sub.substrate_id)
    print("result_bucket:", RESULT_BUCKET)
    print()
    print("preflight...")
    est = sub.preflight(ws)
    print("  cost_usd_max:", f"{est.cost_usd_max:.8f}")
    print("  spot $/hr:   ", est.cost_breakdown.get("spot_price_per_hour_usd"))
    print("  ondemand $/hr:", est.cost_breakdown.get("ondemand_price_per_hour_usd"))
    print("  savings_pct: ", est.cost_breakdown.get("savings_pct"), "%")
    print("  AZ:          ", est.cost_breakdown.get("availability_zone"))
    print()

    print("submitting job and polling (cold start can take 3+ minutes)...")
    result = sub.run(ws)
    print()
    print("=" * 72)
    print("RUN RESULT")
    print("=" * 72)
    print("  status:        ok")
    print("  stdout:       ", repr(result.stdout))
    print("  output_hash:  ", result.output_hash)
    print("  cost_usd:     ", f"{result.cost_usd:.8f}")
    wts = result.evidence["wall_time_sec"]
    print("  wall_time_sec:", f"{wts:.3f}")
    print()
    print("EVIDENCE:")
    for k, v in result.evidence.items():
        print(f"  {k:24s} {v}")
    print()

    result = dataclass_replace(result, issued_at=iso8601_now())
    identity = sign_identity(result=result, signer=signer)
    attestation = build_attestation_dict(
        attestation_id="att_smoke_aws_batch_001",
        result=result,
        identity=identity,
    )

    if as_json:
        print(json.dumps(attestation, indent=2, default=str, sort_keys=True))
        return 0

    from rich.console import Console

    from darwin.agenticcloud.ui import render_attestation_panel_auto

    console = Console()
    console.print()
    console.print(
        f"[dim]smoke ·[/dim] [bold]{sub.substrate_id}[/bold]   "
        f"[dim](real AWS — job_id={result.evidence['job_id']})[/dim]"
    )
    console.print()
    console.print(render_attestation_panel_auto(attestation))
    console.print()
    console.print(
        "[dim]for raw json:[/dim] [bold]AWS_PROFILE=darwin "
        "python infra/batch_smoketest.py --json[/bold]"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
