"""DAC runtime orchestrator.

The single entry point that turns a WorkloadSpec into a SignedAttestation:

    spec → budget check → sandbox.execute → ExecutionResult → signed attestation → store

Every signed attestation (including budget rejections) is persisted to
the AttestationStore for audit and history.
"""

from __future__ import annotations

import time
import uuid

from darwin.agenticcloud.attestation import build_signed_attestation
from darwin.agenticcloud.cost import BudgetExceeded, check_budget, cost_for_seconds
from darwin.agenticcloud.hashing import sha256_hex
from darwin.agenticcloud.sandbox import SUBSTRATE_ID, DockerSandbox, SandboxResult
from darwin.agenticcloud.signing import Signer
from darwin.agenticcloud.storage import AttestationStore
from darwin.agenticcloud.types import ExecutionResult, SignedAttestation, WorkloadSpec


class Runtime:
    """The DAC execution runtime."""

    def __init__(
        self,
        sandbox: DockerSandbox | None = None,
        signer: Signer | None = None,
        store: AttestationStore | None = None,
    ) -> None:
        self._sandbox = sandbox or DockerSandbox()
        self._signer = signer or Signer()
        self._store = store if store is not None else AttestationStore()

    @property
    def signer(self) -> Signer:
        return self._signer

    @property
    def store(self) -> AttestationStore:
        return self._store

    def run(self, spec: WorkloadSpec) -> SignedAttestation:
        """Execute a workload spec and return a signed attestation.

        Every signed attestation (success, error, timeout, cost_exceeded)
        is persisted before returning.
        """
        workload_id = f"wl-{uuid.uuid4().hex[:12]}"

        # Pre-flight budget check (no sandbox launch on rejection)
        try:
            check_budget(spec, SUBSTRATE_ID)
        except BudgetExceeded as e:
            signed = self._build_rejection_attestation(spec, workload_id, str(e))
            self._store.save(signed)
            return signed

        # Execute in the sandbox
        sandbox_result: SandboxResult = self._sandbox.execute(
            code=spec.code,
            language=spec.language,
            timeout_sec=spec.timeout_sec,
            memory_mb=spec.memory_mb,
        )

        cost_usd = cost_for_seconds(sandbox_result.wall_time_sec, sandbox_result.substrate_id)

        execution_result = ExecutionResult(
            workload_id=workload_id,
            status=sandbox_result.status,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            exit_code=sandbox_result.exit_code,
            started_at=sandbox_result.started_at,
            ended_at=sandbox_result.ended_at,
            wall_time_sec=sandbox_result.wall_time_sec,
            cost_usd=cost_usd,
            substrate_id=sandbox_result.substrate_id,
            output_hash=sandbox_result.output_hash,
            error=sandbox_result.error,
        )

        signed = build_signed_attestation(spec, execution_result, self._signer)
        self._store.save(signed)
        return signed

    def _build_rejection_attestation(
        self, spec: WorkloadSpec, workload_id: str, error: str
    ) -> SignedAttestation:
        now = time.time()
        execution_result = ExecutionResult(
            workload_id=workload_id,
            status="cost_exceeded",
            stdout="",
            stderr="",
            exit_code=None,
            started_at=now,
            ended_at=now,
            wall_time_sec=0.0,
            cost_usd=0.0,
            substrate_id=SUBSTRATE_ID,
            output_hash=sha256_hex(b""),
            error=error,
        )
        return build_signed_attestation(spec, execution_result, self._signer)
