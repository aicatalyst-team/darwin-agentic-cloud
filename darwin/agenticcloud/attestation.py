"""Build and verify signed attestations.

An attestation is the cryptographic proof that a workload ran. It binds
together: the workload that was requested, the result that was produced,
the substrate that ran it, the cost, and the signer's identity.

The signature covers the entire attestation payload. Any tampering with
any field breaks verification.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict

from darwin.agenticcloud import ATTESTATION_SCHEMA
from darwin.agenticcloud.hashing import canonical_json, content_hash
from darwin.agenticcloud.signing import Signer, verify_signature
from darwin.agenticcloud.types import Attestation, ExecutionResult, SignedAttestation, WorkloadSpec


def build_signed_attestation(
    spec: WorkloadSpec,
    result: ExecutionResult,
    signer: Signer,
) -> SignedAttestation:
    """Build a signed attestation from a workload spec and execution result."""
    spec_dict = asdict(spec)
    result_dict = asdict(result)

    attestation = Attestation(
        schema=ATTESTATION_SCHEMA,
        attestation_id=str(uuid.uuid4()),
        workload_spec_hash=content_hash(spec_dict),
        workload_spec=spec_dict,
        execution_result=result_dict,
        signer_key_id=signer.key_id(),
        issued_at=time.time(),
    )

    attestation_dict = asdict(attestation)
    canonical = canonical_json(attestation_dict)

    return SignedAttestation(
        attestation=attestation_dict,
        signature_b64=signer.sign(canonical),
        public_key_b64=signer.public_key_b64(),
    )


def verify_attestation(signed: SignedAttestation | dict) -> bool:
    """Verify a signed attestation.

    Returns True if the signature is valid for the attestation payload
    under the included public key. Returns False on any tampering or
    malformed input.

    Accepts either a SignedAttestation dataclass or a dict (as produced
    by serializing one — e.g. from JSON).
    """
    if isinstance(signed, SignedAttestation):
        attestation = signed.attestation
        signature_b64 = signed.signature_b64
        public_key_b64 = signed.public_key_b64
    elif isinstance(signed, dict):
        try:
            attestation = signed["attestation"]
            signature_b64 = signed["signature_b64"]
            public_key_b64 = signed["public_key_b64"]
        except KeyError:
            return False
    else:
        return False

    canonical = canonical_json(attestation)
    return verify_signature(canonical, signature_b64, public_key_b64)
