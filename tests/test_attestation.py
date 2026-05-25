"""End-to-end tests for the attestation layer.

These tests prove the central guarantee of DAC: that a signed attestation
is tamper-evident. Any change to any byte of the signed payload must
cause verification to fail.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from dac.attestation import build_signed_attestation, verify_attestation
from dac.signing import Signer
from dac.types import ExecutionResult, WorkloadSpec


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------
@pytest.fixture
def signer(tmp_path: Path) -> Signer:
    """A Signer with a key in a temporary directory (no global pollution)."""
    return Signer(key_path=tmp_path / "signing.pem")


@pytest.fixture
def spec() -> WorkloadSpec:
    return WorkloadSpec(
        code="print('hello')",
        language="python",
        inputs={"x": 1},
        cost_cap_usd=0.01,
        timeout_sec=30,
        memory_mb=512,
    )


@pytest.fixture
def result() -> ExecutionResult:
    return ExecutionResult(
        workload_id="wl-0001",
        status="ok",
        stdout="hello\n",
        stderr="",
        exit_code=0,
        started_at=1_700_000_000.0,
        ended_at=1_700_000_001.0,
        wall_time_sec=1.0,
        cost_usd=0.0001,
        substrate_id="local-docker",
        output_hash="abc123",
    )


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------
def test_signed_attestation_verifies(signer: Signer, spec: WorkloadSpec, result: ExecutionResult) -> None:
    """A freshly signed attestation verifies."""
    signed = build_signed_attestation(spec, result, signer)
    assert verify_attestation(signed) is True


def test_attestation_includes_required_fields(signer, spec, result) -> None:
    """The attestation payload contains all required fields."""
    signed = build_signed_attestation(spec, result, signer)
    a = signed.attestation
    assert a["schema"].startswith("dac.darwinic.cloud/attestation/")
    assert a["attestation_id"]
    assert a["workload_spec_hash"]
    assert a["workload_spec"] == {
        "code": spec.code,
        "language": spec.language,
        "inputs": spec.inputs,
        "cost_cap_usd": spec.cost_cap_usd,
        "timeout_sec": spec.timeout_sec,
        "memory_mb": spec.memory_mb,
    }
    assert a["execution_result"]["status"] == "ok"
    assert a["signer_key_id"].startswith("dac-local-")
    assert isinstance(a["issued_at"], float)


def test_signer_key_id_is_stable(tmp_path) -> None:
    """A Signer with the same key file produces the same key_id."""
    key_path = tmp_path / "k.pem"
    s1 = Signer(key_path=key_path)
    s2 = Signer(key_path=key_path)
    assert s1.key_id() == s2.key_id()
    assert s1.public_key_b64() == s2.public_key_b64()


def test_different_signers_have_different_key_ids(tmp_path) -> None:
    """Two signers with different keys have different key_ids."""
    s1 = Signer(key_path=tmp_path / "a.pem")
    s2 = Signer(key_path=tmp_path / "b.pem")
    assert s1.key_id() != s2.key_id()


# -------------------------------------------------------------------
# Tamper detection — this is the moat
# -------------------------------------------------------------------
def test_tampering_with_stdout_breaks_verification(signer, spec, result) -> None:
    """Changing the stdout in the attestation breaks the signature."""
    signed = build_signed_attestation(spec, result, signer)
    signed.attestation["execution_result"]["stdout"] = "TAMPERED\n"
    assert verify_attestation(signed) is False


def test_tampering_with_cost_breaks_verification(signer, spec, result) -> None:
    """Changing the cost in the attestation breaks the signature."""
    signed = build_signed_attestation(spec, result, signer)
    signed.attestation["execution_result"]["cost_usd"] = 0.0
    assert verify_attestation(signed) is False


def test_tampering_with_workload_spec_breaks_verification(signer, spec, result) -> None:
    """Changing the workload spec in the attestation breaks the signature."""
    signed = build_signed_attestation(spec, result, signer)
    signed.attestation["workload_spec"]["code"] = "import os; os.system('rm -rf /')"
    assert verify_attestation(signed) is False


def test_tampering_with_signer_key_id_breaks_verification(signer, spec, result) -> None:
    """Changing the claimed signer identity breaks the signature."""
    signed = build_signed_attestation(spec, result, signer)
    signed.attestation["signer_key_id"] = "dac-local-deadbeef00000000"
    assert verify_attestation(signed) is False


def test_swapping_public_key_breaks_verification(signer, spec, result, tmp_path) -> None:
    """Substituting a different public key breaks verification."""
    signed = build_signed_attestation(spec, result, signer)
    other = Signer(key_path=tmp_path / "other.pem")
    signed.public_key_b64 = other.public_key_b64()
    assert verify_attestation(signed) is False


def test_corrupting_signature_breaks_verification(signer, spec, result) -> None:
    """Flipping a bit in the signature breaks verification."""
    signed = build_signed_attestation(spec, result, signer)
    # Replace first character of base64 signature with a different valid one
    original = signed.signature_b64
    flipped = ("B" if original[0] != "B" else "C") + original[1:]
    signed.signature_b64 = flipped
    assert verify_attestation(signed) is False


# -------------------------------------------------------------------
# Serialization round-trip
# -------------------------------------------------------------------
def test_attestation_survives_json_roundtrip(signer, spec, result) -> None:
    """A signed attestation can be serialized to JSON and verified."""
    signed = build_signed_attestation(spec, result, signer)

    as_dict = {
        "attestation": signed.attestation,
        "signature_b64": signed.signature_b64,
        "public_key_b64": signed.public_key_b64,
    }
    serialized = json.dumps(as_dict)
    deserialized = json.loads(serialized)

    assert verify_attestation(deserialized) is True


def test_verify_rejects_malformed_input() -> None:
    """verify_attestation returns False on garbage input, not exceptions."""
    assert verify_attestation({}) is False
    assert verify_attestation({"attestation": {}}) is False
    assert verify_attestation("not even a dict") is False  # type: ignore[arg-type]
    assert verify_attestation(None) is False  # type: ignore[arg-type]


def test_deepcopy_then_verify_still_works(signer, spec, result) -> None:
    """A deep copy of a SignedAttestation verifies the same as the original."""
    signed = build_signed_attestation(spec, result, signer)
    copied = copy.deepcopy(signed)
    assert verify_attestation(copied) is True
