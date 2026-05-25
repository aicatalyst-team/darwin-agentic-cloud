"""DAC runtime orchestrator.

The single entry point that turns a WorkloadSpec into a SignedAttestation:

    spec → budget check → sandbox.execute → ExecutionResult → signed attestation

Pre-flight budget enforcement: workloads whose maximum possible cost
exceeds their cap are rejected before any sandbox is launched. The
rejection is still attested — the agent gets a signed proof that the
workload was rejected and why, with cost_usd=0 and substrate_id intact.
"""

from __future__ import annotations

import time
import uuid

from dac.attestation import build_signed_attestation
from dac.cost import BudgetExceeded, check_budget, cost_for_seconds
from dac.hashing import sha256_hex
from dac.sandbox import SUBSTRATE_ID, DockerSandbox, SandboxResult
from dac.signing import Signer
from dac.types import ExecutionResult, SignedAttestation, WorkloadSpec


class Runtime:
    """The DAC execution runtime."""

    def __init__(
        self,
        sandbox: DockerSandbox | None = None,
        signer: Signer | None = None,
    ) -> None:
        self._sandbox = sandbox or DockerSandbox()
        self._signer = signer or Signer()

    @property
    def signer(self) -> Signer:
        return self._signer

    def run(self, spec: WorkloadSpec) -> SignedAttestation:
        """Execute a workload spec and return a signed attestation."""
        workload_id = f"wl-{uuid.uuid4().hex[:12]}"

        # Pre-flight budget check (cheap, runs before any sandbox launch)
        try:
            check_budget(spec, SUBSTRATE_ID)
        except BudgetExceeded as e:
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
                error=str(e),
            )
            return build_signed_attestation(spec, execution_result, self._signer)

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

        return build_signed_attestation(spec, execution_result, self._signer)
