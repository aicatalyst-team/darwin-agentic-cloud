"""darwin.agenticcloud.router
==============================

Substrate routing — picks which substrate (local, AWS Lambda, Modal,
AWS Batch, etc.) to run a workload on, based on policy.

The router is a pure module: takes a list of Substrate instances and a
WorkloadSpec, returns the chosen substrate plus a routing-decision dict
suitable for embedding in the v0.2 attestation\'s value_added_service
block.

Three policies are shipped in v3.0.0:

- `pick_by_cost`: the default. Asks every candidate to preflight, picks
  the one with the lowest `cost_usd_max`. Substrates that reject the
  workload (PreflightRejected) are excluded.

- `pick_by_substrate`: explicit override. Looks up a substrate by its
  ID or by its short name (e.g. "aws-batch" resolves to
  "aws-batch-ec2-spot-v0-us-east-1" using the default region).

- `pick_by_capability`: capability-based routing. Caller declares
  required features ("long_runtime", "gpu", "tee") and the router
  returns the first substrate that advertises them.

Substrate auto-discovery lives in `discover_substrates()`. Callers that
want zero-config get the auto-discovered list; callers that want
explicit control pass their own list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from darwin.agenticcloud.substrate.base import (
    CostEstimate,
    PreflightRejected,
    Substrate,
    SubstrateError,
)
from darwin.agenticcloud.types import WorkloadSpec

# ============================================================================
# Errors
# ============================================================================


class RouterError(Exception):
    """Base class for router errors."""


class NoSubstrateAvailable(RouterError):
    """No substrate could accept the workload."""


class UnknownSubstrate(RouterError):
    """The requested substrate ID/short-name did not match any candidate."""


# ============================================================================
# Short-name resolution
# ============================================================================


#: Short names that resolve to a substrate ID prefix. The router fills in
#: region/variant suffixes from the candidate list. Keep this list small —
#: it is the public agent-facing surface ("darwin run --substrate aws-batch").
SHORT_NAMES: dict[str, str] = {
    "local": "local-docker-v0",
    "docker": "local-docker-v0",
    "aws-lambda": "aws-lambda",
    "lambda": "aws-lambda",
    "aws-batch": "aws-batch-ec2-spot-v0",
    "batch": "aws-batch-ec2-spot-v0",
    "modal": "modal-v0",
}


def resolve_short_name(name: str, candidates: list[Substrate]) -> Substrate:
    """Resolve a short or full substrate name to a candidate instance.

    The resolution order is:
      1. Exact match on substrate_id (e.g. "aws-batch-ec2-spot-v0-us-east-1")
      2. Short-name expansion + region completion (e.g. "aws-batch" + AWS_REGION)
      3. Prefix match on substrate_id (e.g. "aws-lambda" picks the first
         aws-lambda-* candidate)

    Raises `UnknownSubstrate` if nothing matches.
    """
    # Exact ID match
    for sub in candidates:
        if sub.substrate_id == name:
            return sub

    # Short name expansion
    expanded = SHORT_NAMES.get(name, name)

    # Region completion for AWS substrates
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    candidates_to_try = [
        expanded,
        f"{expanded}-{region}",
    ]

    for tag in candidates_to_try:
        for sub in candidates:
            if sub.substrate_id == tag:
                return sub

    # Prefix match
    for sub in candidates:
        if sub.substrate_id.startswith(expanded):
            return sub

    available = sorted(s.substrate_id for s in candidates)
    raise UnknownSubstrate(
        f"no substrate matching {name!r} (resolved to {expanded!r}). Available: {available}"
    )


# ============================================================================
# Auto-discovery
# ============================================================================


def discover_substrates() -> list[Substrate]:
    """Return the substrates this environment can use right now.

    Discovery is best-effort. A substrate is included if:
      - Its module imports cleanly (deps installed)
      - Its credentials/env vars are present
      - It does not raise on construction

    The discovery order maps to the default routing preference when no
    explicit policy is given: cheapest fast substrate first.
    """
    discovered: list[Substrate] = []

    # local-docker: always try, fails gracefully if Docker not running
    try:
        from darwin.agenticcloud.substrate.local_docker import LocalDockerSubstrate

        discovered.append(LocalDockerSubstrate())
    except Exception:
        pass

    # AWS Lambda: requires AWS credentials
    if _has_aws_credentials():
        try:
            from darwin.agenticcloud.substrate.aws_lambda import LambdaSubstrate

            region = _aws_region()
            discovered.append(LambdaSubstrate(region=region))
        except Exception:
            pass

    # Modal: requires MODAL_TOKEN_ID
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        try:
            from darwin.agenticcloud.substrate.modal import ModalSubstrate

            discovered.append(ModalSubstrate())
        except Exception:
            pass

    # AWS Batch: requires AWS credentials + result bucket env override
    if _has_aws_credentials():
        try:
            from darwin.agenticcloud.substrate.aws_batch import BatchSubstrate

            region = _aws_region()
            account_id = os.environ.get("DARWIN_AWS_ACCOUNT_ID")
            if account_id:
                bucket = f"darwin-batch-results-{account_id}-{region}"
                discovered.append(BatchSubstrate(region=region, result_bucket=bucket))
        except Exception:
            pass

    return discovered


def _has_aws_credentials() -> bool:
    """True if either env-var credentials or an AWS_PROFILE is set."""
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    return bool(os.environ.get("AWS_PROFILE"))


def _aws_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


# ============================================================================
# Routing decision (for VAS block)
# ============================================================================


@dataclass(frozen=True)
class RoutingDecision:
    """Auditable record of why this substrate was chosen.

    Embedded into the v0.2 attestation\'s value_added_service block so
    customers can audit Darwin\'s routing logic post-hoc.
    """

    policy: str  # "pick_by_cost" | "pick_by_substrate" | "pick_by_capability"
    chosen_substrate_id: str
    chosen_reason: str
    candidates_considered: int
    rejected_substrates: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "chosen_substrate_id": self.chosen_substrate_id,
            "chosen_reason": self.chosen_reason,
            "candidates_considered": self.candidates_considered,
            "rejected_substrates": list(self.rejected_substrates),
        }


# ============================================================================
# Policies
# ============================================================================


def pick_by_cost(
    workload: WorkloadSpec,
    candidates: list[Substrate],
) -> tuple[Substrate, CostEstimate, RoutingDecision]:
    """Pick the substrate with the lowest preflight cost estimate.

    Substrates that reject the workload (PreflightRejected) are dropped
    with a recorded reason. Substrates that error during preflight
    (SubstrateError) are also dropped.

    Raises NoSubstrateAvailable if no substrate accepts the workload.
    """
    if not candidates:
        raise NoSubstrateAvailable(
            "no substrate candidates passed to pick_by_cost; "
            "did discover_substrates() return empty?"
        )

    estimates: list[tuple[Substrate, CostEstimate]] = []
    rejected: list[dict[str, str]] = []

    for sub in candidates:
        try:
            est = sub.preflight(workload)
            estimates.append((sub, est))
        except PreflightRejected as e:
            rejected.append({"id": sub.substrate_id, "reason": str(e)})
        except SubstrateError as e:
            rejected.append({"id": sub.substrate_id, "reason": f"error: {e}"})
        except Exception as e:
            rejected.append({"id": sub.substrate_id, "reason": f"unexpected: {type(e).__name__}"})

    if not estimates:
        raise NoSubstrateAvailable(
            f"all {len(candidates)} substrate candidates rejected the workload"
        )

    # Sort by cost; on ties prefer the candidate that appeared earlier
    estimates.sort(key=lambda pair: pair[1].cost_usd_max)
    chosen, chosen_est = estimates[0]

    # Record runner-ups as "rejected" with reason="higher_cost"
    for sub, est in estimates[1:]:
        rejected.append(
            {
                "id": sub.substrate_id,
                "reason": f"higher_cost (${est.cost_usd_max:.6f})",
            }
        )

    decision = RoutingDecision(
        policy="pick_by_cost",
        chosen_substrate_id=chosen.substrate_id,
        chosen_reason=f"lowest_estimated_cost (${chosen_est.cost_usd_max:.6f})",
        candidates_considered=len(candidates),
        rejected_substrates=rejected,
    )
    return chosen, chosen_est, decision


def pick_by_substrate(
    workload: WorkloadSpec,
    candidates: list[Substrate],
    substrate_id: str,
) -> tuple[Substrate, CostEstimate, RoutingDecision]:
    """Pick a specific substrate by ID or short name.

    Short names like "aws-batch" are resolved against the candidate list
    using the AWS_REGION env var to fill in region suffixes.

    Raises UnknownSubstrate if the name cannot be resolved.
    Raises PreflightRejected if the chosen substrate rejects the workload.
    """
    if not candidates:
        raise NoSubstrateAvailable("no substrate candidates passed to pick_by_substrate")

    chosen = resolve_short_name(substrate_id, candidates)
    est = chosen.preflight(workload)  # may raise PreflightRejected — let it bubble

    rejected: list[dict[str, str]] = [
        {"id": sub.substrate_id, "reason": "not_selected"}
        for sub in candidates
        if sub.substrate_id != chosen.substrate_id
    ]

    decision = RoutingDecision(
        policy="pick_by_substrate",
        chosen_substrate_id=chosen.substrate_id,
        chosen_reason=f"explicit_override ({substrate_id!r})",
        candidates_considered=len(candidates),
        rejected_substrates=rejected,
    )
    return chosen, est, decision


def pick_by_capability(
    workload: WorkloadSpec,
    candidates: list[Substrate],
    requires: list[str],
) -> tuple[Substrate, CostEstimate, RoutingDecision]:
    """Pick the first substrate that advertises every required capability.

    Capabilities are declared per-substrate as a frozenset on the Substrate
    instance (e.g. `s.capabilities = {"long_runtime", "gpu"}`). The router
    filters candidates to those whose capabilities are a superset of the
    requires list, then runs pick_by_cost on the survivors.

    Raises NoSubstrateAvailable if no candidate satisfies the requirements.
    """
    eligible = [
        sub for sub in candidates if set(requires).issubset(getattr(sub, "capabilities", set()))
    ]
    rejected = [
        {"id": sub.substrate_id, "reason": "missing_capabilities"}
        for sub in candidates
        if sub not in eligible
    ]

    if not eligible:
        raise NoSubstrateAvailable(
            f"no substrate satisfies capabilities={requires}. "
            f"Considered: {[s.substrate_id for s in candidates]}"
        )

    chosen, est, sub_decision = pick_by_cost(workload, eligible)

    decision = RoutingDecision(
        policy="pick_by_capability",
        chosen_substrate_id=chosen.substrate_id,
        chosen_reason=(
            f"satisfies_capabilities={requires} and lowest_cost (${est.cost_usd_max:.6f})"
        ),
        candidates_considered=len(candidates),
        rejected_substrates=rejected + sub_decision.rejected_substrates,
    )
    return chosen, est, decision
