"""Tests for darwin.agenticcloud.runtime_v02."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Import local-docker so its evidence schema registers with EvidenceRegistry.
import darwin.agenticcloud.substrate.local_docker  # noqa: F401
from darwin.agenticcloud.router import RoutingDecision
from darwin.agenticcloud.runtime_v02 import (
    CostCapExceeded,
    Runtime,
    build_vas_block,
    run,
)
from darwin.agenticcloud.substrate.base import (
    CostEstimate,
    RunResult,
    Substrate,
)
from darwin.agenticcloud.substrate.identity import OperatorFallbackSigner
from darwin.agenticcloud.types import WorkloadSpec


def make_workload(cost_cap_usd=1.0):
    return WorkloadSpec(
        code="print(1)",
        language="python",
        cost_cap_usd=cost_cap_usd,
        timeout_sec=30,
        memory_mb=512,
    )


def make_fake_substrate(substrate_id="local-docker-v0", *, cost_estimate=0.001, actual_cost=0.0008):
    s = MagicMock(spec=Substrate)
    s.substrate_id = substrate_id
    s.substrate_version = "0.1.0"
    s.preflight.return_value = CostEstimate(
        cost_usd_max=cost_estimate,
        cost_breakdown={"compute_usd": cost_estimate},
    )
    s.run.return_value = RunResult(
        substrate_id=substrate_id,
        substrate_version="0.1.0",
        workload_spec_hash="a" * 64,
        output_hash="sha256:" + "b" * 64,
        evidence_schema_id="darwin.cloud/evidence/local-docker/v1",
        evidence={
            "container_status": "ok",
            "exit_code": 0,
            "stdout_hash": "sha256:" + "b" * 64,
            "stderr_hash": "sha256:" + "c" * 64,
            "wall_time_sec": 0.05,
            "image_digest": "sha256:" + "d" * 64,
        },
        cost_usd=actual_cost,
        stdout="output",
        stderr="",
        extensions={},
        tee_required=False,
        issued_at="",
    )
    s.identity_signer.return_value = OperatorFallbackSigner()
    return s


class TestRuntimeRun:
    def test_returns_v02_attestation(self):
        rt = Runtime(substrates=[make_fake_substrate()])
        att = rt.run(make_workload())
        assert att["schema"] == "darwin.cloud/agenticcloud/attestation/v0.2"
        assert att["attestation_id"].startswith("att_")

    def test_outer_signature_present(self):
        rt = Runtime(substrates=[make_fake_substrate()])
        att = rt.run(make_workload())
        assert att["signer_key_id"].startswith("dac-local-")
        assert isinstance(att["signature"], str)
        assert len(att["signature"]) > 0

    def test_vas_block_attached(self):
        rt = Runtime(substrates=[make_fake_substrate()])
        att = rt.run(make_workload())
        vas = att["value_added_service"]
        assert set(vas.keys()) == {
            "identity_signing",
            "cost_cap_enforcement",
            "routing_decision",
        }

    def test_no_substrates_raises(self):
        rt = Runtime(substrates=[])
        with pytest.raises(Exception, match="no substrates available"):
            rt.run(make_workload())


class TestVASBlock:
    def _decision(self):
        return RoutingDecision(
            policy="pick_by_cost",
            chosen_substrate_id="local-docker-v0",
            chosen_reason="lowest",
            candidates_considered=2,
            rejected_substrates=[
                {"id": "aws-batch-ec2-spot-v0-us-east-1", "reason": "higher_cost"},
            ],
        )

    def test_identity_signing_section(self):
        vas = build_vas_block(
            workload=make_workload(),
            cost_estimate_max=0.001,
            actual_cost=0.0008,
            routing_decision=self._decision(),
        )
        assert vas["identity_signing"]["schema_compliant"] is True
        assert "keylist_url" in vas["identity_signing"]

    def test_cost_cap_within(self):
        vas = build_vas_block(
            workload=make_workload(cost_cap_usd=1.0),
            cost_estimate_max=0.001,
            actual_cost=0.0008,
            routing_decision=self._decision(),
        )
        cce = vas["cost_cap_enforcement"]
        assert cce["cap_usd"] == 1.0
        assert cce["actual_usd"] == 0.0008
        assert cce["within_cap"] is True
        assert cce["headroom_usd"] == pytest.approx(0.9992)

    def test_routing_decision_propagated(self):
        d = self._decision()
        vas = build_vas_block(
            workload=make_workload(),
            cost_estimate_max=0.001,
            actual_cost=0.0008,
            routing_decision=d,
        )
        rd = vas["routing_decision"]
        assert rd["policy"] == "pick_by_cost"
        assert rd["candidates_considered"] == 2


class TestCostCapEnforcement:
    def test_estimate_exceeding_cap_raises(self):
        rt = Runtime(substrates=[make_fake_substrate(cost_estimate=10.0)])
        with pytest.raises(CostCapExceeded, match="exceeds cap"):
            rt.run(make_workload(cost_cap_usd=1.0))

    def test_estimate_at_cap_ok(self):
        sub = make_fake_substrate(cost_estimate=0.001, actual_cost=0.001)
        rt = Runtime(substrates=[sub])
        att = rt.run(make_workload(cost_cap_usd=0.001))
        assert att["value_added_service"]["cost_cap_enforcement"]["within_cap"]


class TestRunHelper:
    def test_helper_uses_discovered_substrates(self, monkeypatch):
        sub = make_fake_substrate()
        from darwin.agenticcloud import runtime_v02

        monkeypatch.setattr(runtime_v02, "discover_substrates", lambda: [sub])
        att = run("print(1)")
        assert att["execution_result"]["substrate"]["id"] == "local-docker-v0"

    def test_helper_defaults_applied(self, monkeypatch):
        captured = {}

        def fake_run(self, workload, *, substrate=None):
            captured["spec"] = workload
            return {"stub": True}

        monkeypatch.setattr(Runtime, "run", fake_run)
        sub = make_fake_substrate()
        from darwin.agenticcloud import runtime_v02

        monkeypatch.setattr(runtime_v02, "discover_substrates", lambda: [sub])
        run("print(1)")
        spec = captured["spec"]
        assert spec.language == "python"
        assert spec.cost_cap_usd == 0.10
        assert spec.timeout_sec == 30
        assert spec.memory_mb == 512
