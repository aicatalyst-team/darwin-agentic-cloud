"""Cost model and budget enforcement for DAC.

Costs are computed per substrate. For v0, local Docker has a flat
wall-time rate. Real substrates (GPU, decentralized providers, sovereign
clouds) will have richer rate cards in v0.2.

Enforcement:
- Pre-flight: max_possible_cost(spec) is computed before sandbox launch.
  If it would exceed the cap, we reject with BudgetExceeded — no
  sandbox launched, no resources consumed.
- In-flight: tracked in v0.2 when variable-rate substrates land.
"""

from __future__ import annotations

from darwin.agenticcloud.types import WorkloadSpec

# Rate card: USD per wall-time second by substrate.
# Tracked separately from substrate identity so a substrate can update
# its rate over time without breaking attestation verifiability.
SUBSTRATE_RATES_PER_SEC: dict[str, float] = {
    "local-docker-v0": 0.0001,
}

DEFAULT_RATE_PER_SEC = 0.0001


class BudgetExceeded(Exception):
    """Raised when a workload's cost cap is or would be exceeded."""

    def __init__(self, message: str, *, projected_usd: float, cap_usd: float) -> None:
        super().__init__(message)
        self.projected_usd = projected_usd
        self.cap_usd = cap_usd


def rate_for_substrate(substrate_id: str) -> float:
    """Return USD per wall-time second for a substrate."""
    return SUBSTRATE_RATES_PER_SEC.get(substrate_id, DEFAULT_RATE_PER_SEC)


def cost_for_seconds(seconds: float, substrate_id: str) -> float:
    """Compute the cost (USD) for a given wall-time duration on a substrate."""
    rate = rate_for_substrate(substrate_id)
    return round(seconds * rate, 8)


def max_possible_cost(spec: WorkloadSpec, substrate_id: str) -> float:
    """The largest cost this workload could incur if it runs to its timeout."""
    return cost_for_seconds(spec.timeout_sec, substrate_id)


def check_budget(spec: WorkloadSpec, substrate_id: str) -> None:
    """Pre-flight: raise BudgetExceeded if the workload's max cost exceeds its cap.

    This is the cheap, defensive check. It runs before any sandbox is
    launched. It rejects requests where timeout * rate would exceed cap,
    so a hallucinating agent that asks for a 600-second timeout under a
    $0.01 cap gets stopped before consuming any resources.
    """
    if spec.cost_cap_usd <= 0:
        raise BudgetExceeded(
            f"cost_cap_usd must be > 0; got {spec.cost_cap_usd}",
            projected_usd=0.0,
            cap_usd=spec.cost_cap_usd,
        )

    projected = max_possible_cost(spec, substrate_id)
    if projected > spec.cost_cap_usd:
        raise BudgetExceeded(
            f"Projected max cost ${projected:.8f} exceeds cap ${spec.cost_cap_usd:.8f} "
            f"(timeout={spec.timeout_sec}s @ ${rate_for_substrate(substrate_id):.8f}/s "
            f"on {substrate_id})",
            projected_usd=projected,
            cap_usd=spec.cost_cap_usd,
        )
