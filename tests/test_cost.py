"""Tests for cost model and budget enforcement.

The central guarantee: a workload whose maximum possible cost exceeds
its cap is rejected before any sandbox is launched, and the rejection
itself is signed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from darwin.agenticcloud.attestation import verify_attestation
from darwin.agenticcloud.cost import (
    BudgetExceeded,
    check_budget,
    cost_for_seconds,
    max_possible_cost,
    rate_for_substrate,
)
from darwin.agenticcloud.storage import AttestationStore
from darwin.agenticcloud.types import ExecutionResult, WorkloadSpec

if TYPE_CHECKING:
    from pathlib import Path


SUBSTRATE = "local-docker-v0"


# -------------------------------------------------------------------
# Pure cost math
# -------------------------------------------------------------------
def test_rate_for_known_substrate() -> None:
    assert rate_for_substrate(SUBSTRATE) == 0.0001


def test_rate_for_unknown_substrate_uses_default() -> None:
    # Unknown substrates fall back to the default rate; never zero.
    rate = rate_for_substrate("nonexistent-substrate")
    assert rate > 0


def test_cost_for_seconds_is_deterministic() -> None:
    assert cost_for_seconds(10.0, SUBSTRATE) == cost_for_seconds(10.0, SUBSTRATE)
    assert cost_for_seconds(0.0, SUBSTRATE) == 0.0


def test_max_possible_cost_uses_timeout() -> None:
    spec = WorkloadSpec(code="x=1", timeout_sec=30, cost_cap_usd=0.01)
    assert max_possible_cost(spec, SUBSTRATE) == 0.003  # 30 * 0.0001


# -------------------------------------------------------------------
# check_budget — the enforcement primitive
# -------------------------------------------------------------------
def test_budget_passes_when_max_cost_under_cap() -> None:
    spec = WorkloadSpec(code="print(1)", timeout_sec=10, cost_cap_usd=0.01)
    # No exception
    check_budget(spec, SUBSTRATE)


def test_budget_passes_at_exact_boundary() -> None:
    # 30s * 0.0001 = 0.003; cap 0.003 is exactly at the limit, must pass.
    spec = WorkloadSpec(code="print(1)", timeout_sec=30, cost_cap_usd=0.003)
    check_budget(spec, SUBSTRATE)


def test_budget_rejects_when_timeout_too_long_for_cap() -> None:
    # 600s timeout * 0.0001 = 0.06; cap 0.01 — must reject.
    spec = WorkloadSpec(code="print(1)", timeout_sec=600, cost_cap_usd=0.01)
    with pytest.raises(BudgetExceeded) as exc_info:
        check_budget(spec, SUBSTRATE)
    assert exc_info.value.projected_usd == 0.06
    assert exc_info.value.cap_usd == 0.01


def test_budget_rejects_zero_or_negative_cap() -> None:
    spec = WorkloadSpec(code="print(1)", timeout_sec=1, cost_cap_usd=0.0)
    with pytest.raises(BudgetExceeded):
        check_budget(spec, SUBSTRATE)

    spec_neg = WorkloadSpec(code="print(1)", timeout_sec=1, cost_cap_usd=-0.01)
    with pytest.raises(BudgetExceeded):
        check_budget(spec_neg, SUBSTRATE)


# -------------------------------------------------------------------
# Runtime integration — the agent-facing guarantee
# -------------------------------------------------------------------
def test_runtime_rejects_budget_with_signed_attestation(tmp_path: "Path") -> None:
    """A budget-exceeded workload returns a signed attestation with
    status='cost_exceeded' and never launches the sandbox."""
    from darwin.agenticcloud.runtime import Runtime
    from darwin.agenticcloud.sandbox import SUBSTRATE_ID
    from darwin.agenticcloud.signing import Signer

    # Use a stub sandbox that would fail loudly if called
    class ShouldNotBeCalledSandbox:
        def execute(self, *args, **kwargs):
            raise AssertionError("Sandbox must not be called when budget is exceeded")

    runtime = Runtime(
        sandbox=ShouldNotBeCalledSandbox(),  # type: ignore[arg-type]
        signer=Signer(key_path=tmp_path / "key.pem"),
        store=AttestationStore(db_path=tmp_path / "att.db"),
    )

    spec = WorkloadSpec(
        code="print('would never run')",
        timeout_sec=600,
        cost_cap_usd=0.01,
    )
    signed = runtime.run(spec)

    # The rejection is structured and signed
    er = signed.attestation["execution_result"]
    assert er["status"] == "cost_exceeded"
    assert er["cost_usd"] == 0.0
    assert er["wall_time_sec"] == 0.0
    assert er["substrate_id"] == SUBSTRATE_ID
    assert "Projected max cost" in er["error"]

    # And the rejection attestation itself verifies
    assert verify_attestation(signed) is True
