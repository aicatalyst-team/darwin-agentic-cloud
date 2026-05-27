"""darwin.agenticcloud.runtime_v02
====================================

v0.2 runtime — composes substrate routing, execution, identity signing,
value-added-service stamping, and outer attestation signing into a single
``Runtime.run()`` call.

The v0.1 ``Runtime`` in ``runtime.py`` remains for backward compatibility.
This module is the new path that produces v0.2 attestations with the VAS
block.

Public API:

- ``Runtime`` — class-level orchestrator, useful for tests and advanced use
- ``run(code, substrate=..., cost_cap=..., timeout=..., memory_mb=...)`` —
  top-level one-liner re-exported from ``darwin`` for agent use:

    .. code-block:: python

        from darwin import run
        attestation = run("print(\'hi\')")

The VAS block embedded in every v0.2 attestation contains three fields
(all real today, see Phase 2 spec for v3.1+ additions):

- ``identity_signing``: substrate identity signature is anchored to the
  darwin.cloud keylist (always present).
- ``cost_cap_enforcement``: declared cap, actual cost, within-cap flag,
  headroom remaining.
- ``routing_decision``: which policy chose this substrate, why, and which
  substrates were considered or rejected.
"""

from __future__ import annotations

import uuid
from dataclasses import replace as dataclass_replace
from typing import Any

from darwin.agenticcloud.hashing import canonical_json
from darwin.agenticcloud.router import (
    RoutingDecision,
    discover_substrates,
    pick_by_cost,
    pick_by_substrate,
)
from darwin.agenticcloud.signing import Signer
from darwin.agenticcloud.substrate.base import (
    SUBSTRATE_KEYLIST_URL,
    Substrate,
    build_attestation_dict,
    iso8601_now,
    sign_identity,
)
from darwin.agenticcloud.types import WorkloadSpec

# ============================================================================
# Errors
# ============================================================================


class RuntimeError_v02(Exception):
    """Base class for v0.2 runtime errors. Suffixed to avoid clashing with
    Python\'s built-in RuntimeError."""


class CostCapExceeded(RuntimeError_v02):
    """Preflight estimate exceeded the declared cost cap."""


# ============================================================================
# Defaults — Apple-grade, set so an agent can call run() with no kwargs
# ============================================================================


DEFAULT_COST_CAP_USD = 0.10
DEFAULT_TIMEOUT_SEC = 30
DEFAULT_MEMORY_MB = 512
DEFAULT_LANGUAGE = "python"


# ============================================================================
# Runtime
# ============================================================================


class Runtime:
    """v0.2 orchestrator producing signed v0.2 attestations.

    Construction is zero-arg by default — substrates are auto-discovered
    and the operator signer is generated on demand. Pass an explicit
    ``substrates=...`` list for deterministic test setups.
    """

    def __init__(
        self,
        substrates: list[Substrate] | None = None,
        operator_signer: Signer | None = None,
    ) -> None:
        self._substrates = list(substrates) if substrates is not None else discover_substrates()
        self._operator_signer = operator_signer or Signer()

    @property
    def substrates(self) -> list[Substrate]:
        return list(self._substrates)

    @property
    def operator_signer(self) -> Signer:
        return self._operator_signer

    def run(
        self,
        workload: WorkloadSpec,
        *,
        substrate: str | None = None,
    ) -> dict[str, Any]:
        """Execute a workload and return a signed v0.2 attestation dict.

        ``substrate`` is an optional override (full ID or short name like
        ``"aws-batch"``). When omitted, the cheapest available substrate
        is picked via ``pick_by_cost``.
        """
        if not self._substrates:
            raise RuntimeError_v02(
                "no substrates available — install Docker, set AWS credentials, "
                "or pass substrates=... explicitly"
            )

        # --- Route ---
        if substrate is not None:
            chosen, est, decision = pick_by_substrate(workload, self._substrates, substrate)
        else:
            chosen, est, decision = pick_by_cost(workload, self._substrates)

        # --- Cost cap enforcement (early) ---
        cap = workload.cost_cap_usd
        if est.cost_usd_max > cap:
            raise CostCapExceeded(
                f"preflight cost ${est.cost_usd_max:.6f} exceeds cap ${cap:.6f} "
                f"on substrate {chosen.substrate_id}"
            )

        # --- Execute ---
        result = chosen.run(workload)

        # --- Stamp issued_at + sign identity ---
        issued_at = iso8601_now()
        result = dataclass_replace(result, issued_at=issued_at)
        identity = sign_identity(result=result, signer=chosen.identity_signer())

        # --- Build attestation dict (inner) ---
        attestation_id = f"att_{uuid.uuid4().hex[:16]}"
        attestation = build_attestation_dict(
            attestation_id=attestation_id,
            result=result,
            identity=identity,
        )

        # --- Stamp Value-Added Service block ---
        attestation["value_added_service"] = build_vas_block(
            workload=workload,
            cost_estimate_max=est.cost_usd_max,
            actual_cost=result.cost_usd,
            routing_decision=decision,
        )

        # --- Outer (operator) signature ---
        canonical = canonical_json(attestation)
        outer_sig_b64 = self._operator_signer.sign(canonical)
        attestation["signer_key_id"] = self._operator_signer.key_id()
        attestation["signature"] = outer_sig_b64

        return attestation


# ============================================================================
# VAS block construction
# ============================================================================


def build_vas_block(
    *,
    workload: WorkloadSpec,
    cost_estimate_max: float,
    actual_cost: float,
    routing_decision: RoutingDecision,
) -> dict[str, Any]:
    """Construct the value_added_service block.

    Three fields, all backed by real Darwin behavior — not marketing:

    - ``identity_signing``: every substrate signs its identity with a key
      anchored to the public keylist. Auditable post-hoc.
    - ``cost_cap_enforcement``: the runtime refused to run if the preflight
      estimate exceeded the cap, and recorded the actual vs declared cost.
    - ``routing_decision``: which substrate was picked and why.
    """
    cap = float(workload.cost_cap_usd)
    actual = float(actual_cost)
    headroom = cap - actual
    return {
        "identity_signing": {
            "schema_compliant": True,
            "keylist_url": SUBSTRATE_KEYLIST_URL,
        },
        "cost_cap_enforcement": {
            "cap_usd": round(cap, 8),
            "estimated_usd_max": round(float(cost_estimate_max), 8),
            "actual_usd": round(actual, 8),
            "within_cap": actual <= cap,
            "headroom_usd": round(headroom, 8),
        },
        "routing_decision": routing_decision.to_dict(),
    }


# ============================================================================
# Top-level agent helper — `from darwin import run`
# ============================================================================


def run(
    code: str,
    *,
    substrate: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    cost_cap: float = DEFAULT_COST_CAP_USD,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    memory_mb: int = DEFAULT_MEMORY_MB,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute ``code`` and return a signed v0.2 attestation dict.

    This is the Apple-grade agent API. Zero required kwargs beyond the
    code itself. Substrate auto-picked unless overridden.

    .. code-block:: python

        from darwin import run

        att = run("print(\'hello\')")
        att = run("print(\'big job\')", substrate="aws-batch")
        att = run(open("script.py").read(), cost_cap=1.0, timeout=300)
    """
    workload = WorkloadSpec(
        code=code,
        language=language,
        cost_cap_usd=cost_cap,
        timeout_sec=timeout,
        memory_mb=memory_mb,
        inputs=inputs or {},
    )
    return Runtime().run(workload, substrate=substrate)
