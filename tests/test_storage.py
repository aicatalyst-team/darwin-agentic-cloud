"""Tests for the attestation store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from darwin.agenticcloud.attestation import build_signed_attestation, verify_attestation
from darwin.agenticcloud.signing import Signer
from darwin.agenticcloud.storage import AttestationStore
from darwin.agenticcloud.types import ExecutionResult, WorkloadSpec

if TYPE_CHECKING:
    from pathlib import Path


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _make_signed(signer: Signer, *, status: str = "ok", cost: float = 0.001):
    spec = WorkloadSpec(code="print(1)", timeout_sec=10, cost_cap_usd=0.01)
    result = ExecutionResult(
        workload_id="wl-test-0001",
        status=status,
        stdout="1\n",
        stderr="",
        exit_code=0 if status == "ok" else None,
        started_at=1_700_000_000.0,
        ended_at=1_700_000_001.0,
        wall_time_sec=1.0,
        cost_usd=cost,
        substrate_id="local-docker-v0",
        output_hash="abc123",
    )
    return build_signed_attestation(spec, result, signer)


@pytest.fixture
def signer(tmp_path: "Path") -> Signer:
    return Signer(key_path=tmp_path / "signing.pem")


@pytest.fixture
def store(tmp_path: "Path") -> AttestationStore:
    return AttestationStore(db_path=tmp_path / "att.db")


# -------------------------------------------------------------------
# Schema and basic round-trip
# -------------------------------------------------------------------
def test_store_creates_db_file(tmp_path: "Path") -> None:
    db_path = tmp_path / "subdir" / "att.db"
    store = AttestationStore(db_path=db_path)
    assert db_path.exists()
    assert store.count() == 0


def test_save_and_get(store: AttestationStore, signer: Signer) -> None:
    signed = _make_signed(signer)
    store.save(signed)

    fetched = store.get(signed.attestation["attestation_id"])
    assert fetched is not None
    assert fetched.attestation_id == signed.attestation["attestation_id"]
    assert fetched.status == "ok"
    assert fetched.substrate_id == "local-docker-v0"


def test_stored_attestation_still_verifies(store: AttestationStore, signer: Signer) -> None:
    """An attestation pulled out of storage still verifies cryptographically."""
    signed = _make_signed(signer)
    store.save(signed)
    fetched = store.get(signed.attestation["attestation_id"])

    assert fetched is not None
    assert verify_attestation(fetched.signed_attestation) is True


def test_get_returns_none_for_missing(store: AttestationStore) -> None:
    assert store.get("does-not-exist") is None


def test_save_is_idempotent(store: AttestationStore, signer: Signer) -> None:
    """Saving the same attestation twice doesn't create duplicates."""
    signed = _make_signed(signer)
    store.save(signed)
    store.save(signed)
    assert store.count() == 1


# -------------------------------------------------------------------
# Queries
# -------------------------------------------------------------------
def test_list_recent_orders_newest_first(store: AttestationStore, signer: Signer) -> None:
    import time

    sig1 = _make_signed(signer)
    time.sleep(0.001)
    sig2 = _make_signed(signer)
    store.save(sig1)
    store.save(sig2)

    recent = store.list_recent(limit=10)
    assert len(recent) == 2
    assert recent[0].issued_at >= recent[1].issued_at


def test_list_by_status(store: AttestationStore, signer: Signer) -> None:
    store.save(_make_signed(signer, status="ok"))
    store.save(_make_signed(signer, status="cost_exceeded"))
    store.save(_make_signed(signer, status="ok"))

    rejected = store.list_by_status("cost_exceeded")
    assert len(rejected) == 1
    assert rejected[0].status == "cost_exceeded"

    ok = store.list_by_status("ok")
    assert len(ok) == 2


def test_list_by_signer(store: AttestationStore, signer: Signer, tmp_path: "Path") -> None:
    other_signer = Signer(key_path=tmp_path / "other.pem")
    store.save(_make_signed(signer))
    store.save(_make_signed(signer))
    store.save(_make_signed(other_signer))

    mine = store.list_by_signer(signer.key_id())
    assert len(mine) == 2

    theirs = store.list_by_signer(other_signer.key_id())
    assert len(theirs) == 1


def test_total_cost(store: AttestationStore, signer: Signer) -> None:
    store.save(_make_signed(signer, cost=0.001))
    store.save(_make_signed(signer, cost=0.002))
    store.save(_make_signed(signer, cost=0.003))

    assert store.total_cost_usd() == pytest.approx(0.006)


def test_total_cost_by_signer(
    store: AttestationStore, signer: Signer, tmp_path: "Path"
) -> None:
    other = Signer(key_path=tmp_path / "other.pem")
    store.save(_make_signed(signer, cost=0.001))
    store.save(_make_signed(signer, cost=0.002))
    store.save(_make_signed(other, cost=0.005))

    assert store.total_cost_usd(signer.key_id()) == pytest.approx(0.003)
    assert store.total_cost_usd(other.key_id()) == pytest.approx(0.005)


# -------------------------------------------------------------------
# Runtime integration
# -------------------------------------------------------------------
def test_runtime_persists_every_attestation(tmp_path: "Path") -> None:
    """Runtime.run() writes the attestation to the store before returning."""
    from darwin.agenticcloud.runtime import Runtime

    store = AttestationStore(db_path=tmp_path / "att.db")
    signer = Signer(key_path=tmp_path / "key.pem")

    # Use a stub sandbox that returns deterministic output without Docker
    class StubSandbox:
        def execute(self, code, language, timeout_sec, memory_mb):
            from darwin.agenticcloud.sandbox import SUBSTRATE_ID, SandboxResult

            return SandboxResult(
                status="ok",
                stdout="hi\n",
                stderr="",
                exit_code=0,
                started_at=1.0,
                ended_at=1.1,
                wall_time_sec=0.1,
                substrate_id=SUBSTRATE_ID,
                output_hash="hashed",
            )

    runtime = Runtime(sandbox=StubSandbox(), signer=signer, store=store)  # type: ignore[arg-type]
    spec = WorkloadSpec(code="print('hi')", timeout_sec=10, cost_cap_usd=0.01)
    signed = runtime.run(spec)

    assert store.count() == 1
    fetched = store.get(signed.attestation["attestation_id"])
    assert fetched is not None
    assert fetched.status == "ok"


def test_runtime_persists_rejected_attestations(tmp_path: "Path") -> None:
    """Even budget-rejected workloads are persisted."""
    from darwin.agenticcloud.runtime import Runtime

    store = AttestationStore(db_path=tmp_path / "att.db")
    signer = Signer(key_path=tmp_path / "key.pem")

    class ShouldNotBeCalledSandbox:
        def execute(self, *args, **kwargs):
            raise AssertionError("Sandbox must not be called on budget rejection")

    runtime = Runtime(sandbox=ShouldNotBeCalledSandbox(), signer=signer, store=store)  # type: ignore[arg-type]
    spec = WorkloadSpec(code="print(1)", timeout_sec=600, cost_cap_usd=0.01)
    signed = runtime.run(spec)

    assert signed.attestation["execution_result"]["status"] == "cost_exceeded"
    assert store.count() == 1
    fetched = store.get(signed.attestation["attestation_id"])
    assert fetched is not None
    assert fetched.status == "cost_exceeded"
