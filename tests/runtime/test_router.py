"""tests.runtime.test_router
=============================

Tests for darwin.agenticcloud.router.

Coverage:
- TestShortNameResolution: exact / short / prefix / region completion / unknown
- TestPickByCost: happy path, ties, all-rejected, mix of accept+reject
- TestPickBySubstrate: explicit override, short-name, unknown, preflight rejection
- TestPickByCapability: capability filtering, no eligible, falls through to cost
- TestRoutingDecision: dict shape, fields populated
- TestDiscoverSubstrates: env-var gating (no live boto3 calls)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from darwin.agenticcloud.router import (
    NoSubstrateAvailable,
    RoutingDecision,
    UnknownSubstrate,
    discover_substrates,
    pick_by_capability,
    pick_by_cost,
    pick_by_substrate,
    resolve_short_name,
)
from darwin.agenticcloud.substrate.base import (
    CostEstimate,
    PreflightRejected,
    Substrate,
    SubstrateError,
)
from darwin.agenticcloud.types import WorkloadSpec

# ============================================================================
# Helpers / fakes
# ============================================================================


def make_workload() -> WorkloadSpec:
    return WorkloadSpec(
        code="print(1)",
        language="python",
        cost_cap_usd=1.0,
        timeout_sec=30,
        memory_mb=512,
    )


def make_fake_substrate(
    substrate_id: str,
    *,
    cost: float = 0.001,
    rejects: bool = False,
    errors: bool = False,
    capabilities: set[str] | None = None,
) -> MagicMock:
    s = MagicMock(spec=Substrate)
    s.substrate_id = substrate_id
    s.substrate_version = "0.1.0"
    s.capabilities = capabilities or set()
    if rejects:
        s.preflight.side_effect = PreflightRejected(f"{substrate_id} rejects this workload")
    elif errors:
        s.preflight.side_effect = SubstrateError(f"{substrate_id} backend unreachable")
    else:
        s.preflight.return_value = CostEstimate(
            cost_usd_max=cost,
            cost_breakdown={"compute_usd": cost},
        )
    return s


# ============================================================================
# TestShortNameResolution
# ============================================================================


class TestShortNameResolution:
    def test_exact_id_match(self) -> None:
        s = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1")
        assert resolve_short_name("aws-batch-ec2-spot-v0-us-east-1", [s]) is s

    def test_short_name_with_region_completion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        s = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1")
        assert resolve_short_name("aws-batch", [s]) is s

    def test_short_name_lambda_resolves_to_lambda(self) -> None:
        s = make_fake_substrate("aws-lambda-us-east-1")
        assert resolve_short_name("aws-lambda", [s]) is s

    def test_short_name_modal(self) -> None:
        s = make_fake_substrate("modal-v0")
        assert resolve_short_name("modal", [s]) is s

    def test_short_name_local(self) -> None:
        s = make_fake_substrate("local-docker-v0")
        assert resolve_short_name("local", [s]) is s
        assert resolve_short_name("docker", [s]) is s

    def test_prefix_match_picks_first(self) -> None:
        s1 = make_fake_substrate("aws-lambda-us-east-1")
        s2 = make_fake_substrate("aws-lambda-us-west-2")
        # Prefix match returns the first matching candidate by list order
        result = resolve_short_name("aws-lambda", [s1, s2])
        assert result is s1

    def test_unknown_substrate_raises(self) -> None:
        s = make_fake_substrate("local-docker-v0")
        with pytest.raises(UnknownSubstrate, match="no substrate matching"):
            resolve_short_name("akash", [s])


# ============================================================================
# TestPickByCost
# ============================================================================


class TestPickByCost:
    def test_picks_lowest_cost(self) -> None:
        lambda_sub = make_fake_substrate("aws-lambda-us-east-1", cost=0.00001)
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1", cost=0.001)
        local_sub = make_fake_substrate("local-docker-v0", cost=0.0)

        chosen, est, decision = pick_by_cost(make_workload(), [batch_sub, lambda_sub, local_sub])
        assert chosen is local_sub
        assert est.cost_usd_max == 0.0
        assert decision.chosen_substrate_id == "local-docker-v0"

    def test_excludes_rejecting_substrates(self) -> None:
        lambda_sub = make_fake_substrate("aws-lambda-us-east-1", cost=0.0001)
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1", rejects=True)

        chosen, _est, decision = pick_by_cost(make_workload(), [lambda_sub, batch_sub])
        assert chosen is lambda_sub
        rejected_ids = [r["id"] for r in decision.rejected_substrates]
        assert "aws-batch-ec2-spot-v0-us-east-1" in rejected_ids

    def test_excludes_erroring_substrates(self) -> None:
        local_sub = make_fake_substrate("local-docker-v0", cost=0.0)
        modal_sub = make_fake_substrate("modal-v0", errors=True)

        chosen, _est, decision = pick_by_cost(make_workload(), [local_sub, modal_sub])
        assert chosen is local_sub
        rejected_ids = [r["id"] for r in decision.rejected_substrates]
        assert "modal-v0" in rejected_ids

    def test_all_rejected_raises(self) -> None:
        s1 = make_fake_substrate("a", rejects=True)
        s2 = make_fake_substrate("b", rejects=True)
        with pytest.raises(NoSubstrateAvailable, match="all 2 substrate candidates rejected"):
            pick_by_cost(make_workload(), [s1, s2])

    def test_empty_candidates_raises(self) -> None:
        with pytest.raises(NoSubstrateAvailable, match="no substrate candidates"):
            pick_by_cost(make_workload(), [])

    def test_tie_prefers_earlier_in_list(self) -> None:
        s1 = make_fake_substrate("first", cost=0.001)
        s2 = make_fake_substrate("second", cost=0.001)
        chosen, _est, _decision = pick_by_cost(make_workload(), [s1, s2])
        assert chosen is s1


# ============================================================================
# TestPickBySubstrate
# ============================================================================


class TestPickBySubstrate:
    def test_explicit_full_id_match(self) -> None:
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1")
        lambda_sub = make_fake_substrate("aws-lambda-us-east-1")

        chosen, _est, decision = pick_by_substrate(
            make_workload(),
            [batch_sub, lambda_sub],
            "aws-batch-ec2-spot-v0-us-east-1",
        )
        assert chosen is batch_sub
        assert decision.policy == "pick_by_substrate"

    def test_short_name_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1")
        lambda_sub = make_fake_substrate("aws-lambda-us-east-1")

        chosen, _est, _decision = pick_by_substrate(
            make_workload(), [batch_sub, lambda_sub], "aws-batch"
        )
        assert chosen is batch_sub

    def test_unknown_name_raises(self) -> None:
        s = make_fake_substrate("local-docker-v0")
        with pytest.raises(UnknownSubstrate):
            pick_by_substrate(make_workload(), [s], "akash")

    def test_explicit_substrate_can_still_reject_preflight(self) -> None:
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1", rejects=True)
        # PreflightRejected should bubble — the agent explicitly asked for this
        # substrate; honest failure is better than silent fallback.
        with pytest.raises(PreflightRejected):
            pick_by_substrate(
                make_workload(),
                [batch_sub],
                "aws-batch-ec2-spot-v0-us-east-1",
            )

    def test_decision_records_other_candidates_as_not_selected(self) -> None:
        batch_sub = make_fake_substrate("aws-batch-ec2-spot-v0-us-east-1")
        lambda_sub = make_fake_substrate("aws-lambda-us-east-1")

        _chosen, _est, decision = pick_by_substrate(
            make_workload(),
            [batch_sub, lambda_sub],
            "aws-batch-ec2-spot-v0-us-east-1",
        )
        rejected = decision.rejected_substrates
        assert {r["id"] for r in rejected} == {"aws-lambda-us-east-1"}
        assert all(r["reason"] == "not_selected" for r in rejected)


# ============================================================================
# TestPickByCapability
# ============================================================================


class TestPickByCapability:
    def test_picks_substrate_with_required_capability(self) -> None:
        cpu_only = make_fake_substrate("aws-lambda-us-east-1", cost=0.0001, capabilities=set())
        long_runtime = make_fake_substrate(
            "aws-batch-ec2-spot-v0-us-east-1",
            cost=0.001,
            capabilities={"long_runtime"},
        )
        chosen, _est, decision = pick_by_capability(
            make_workload(),
            [cpu_only, long_runtime],
            requires=["long_runtime"],
        )
        assert chosen is long_runtime
        assert decision.policy == "pick_by_capability"

    def test_no_eligible_substrate_raises(self) -> None:
        cpu_only = make_fake_substrate("aws-lambda-us-east-1", capabilities=set())
        with pytest.raises(NoSubstrateAvailable, match="no substrate satisfies"):
            pick_by_capability(make_workload(), [cpu_only], requires=["gpu"])

    def test_among_eligible_picks_lowest_cost(self) -> None:
        cheap = make_fake_substrate("modal-v0", cost=0.00006, capabilities={"long_runtime", "gpu"})
        expensive = make_fake_substrate(
            "aws-batch-ec2-spot-v0-us-east-1",
            cost=0.001,
            capabilities={"long_runtime", "gpu"},
        )
        chosen, _est, _decision = pick_by_capability(
            make_workload(),
            [expensive, cheap],
            requires=["long_runtime", "gpu"],
        )
        assert chosen is cheap


# ============================================================================
# TestRoutingDecision
# ============================================================================


class TestRoutingDecision:
    def test_to_dict_shape(self) -> None:
        d = RoutingDecision(
            policy="pick_by_cost",
            chosen_substrate_id="local-docker-v0",
            chosen_reason="lowest_estimated_cost ($0.000000)",
            candidates_considered=3,
            rejected_substrates=[
                {"id": "aws-batch-ec2-spot-v0-us-east-1", "reason": "higher_cost"},
            ],
        )
        out = d.to_dict()
        assert out["policy"] == "pick_by_cost"
        assert out["chosen_substrate_id"] == "local-docker-v0"
        assert out["candidates_considered"] == 3
        assert len(out["rejected_substrates"]) == 1
        assert out["rejected_substrates"][0]["id"] == ("aws-batch-ec2-spot-v0-us-east-1")

    def test_decision_carries_through_pick_by_cost(self) -> None:
        s = make_fake_substrate("local-docker-v0", cost=0.0)
        _chosen, _est, decision = pick_by_cost(make_workload(), [s])
        d = decision.to_dict()
        assert d["policy"] == "pick_by_cost"
        assert d["chosen_substrate_id"] == "local-docker-v0"
        assert d["candidates_considered"] == 1


# ============================================================================
# TestDiscoverSubstrates (env-var gating)
# ============================================================================


class TestDiscoverSubstrates:
    def test_returns_a_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip every AWS / Modal env var so only local-docker is discoverable
        for var in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_PROFILE",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "MODAL_TOKEN_ID",
            "MODAL_TOKEN_SECRET",
            "DARWIN_AWS_ACCOUNT_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        # local-docker may or may not be present depending on whether Docker
        # is running; the function should still return a list either way.
        result = discover_substrates()
        assert isinstance(result, list)

    def test_modal_requires_both_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
        monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
        result = discover_substrates()
        modal_ids = [s.substrate_id for s in result if "modal" in s.substrate_id]
        assert modal_ids == []

    def test_aws_batch_requires_account_id_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even with AWS creds, Batch needs DARWIN_AWS_ACCOUNT_ID to know the bucket
        monkeypatch.delenv("DARWIN_AWS_ACCOUNT_ID", raising=False)
        result = discover_substrates()
        batch_ids = [s.substrate_id for s in result if "batch" in s.substrate_id]
        assert batch_ids == []
