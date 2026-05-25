"""DAC runtime orchestrator.

The single entry point that turns a WorkloadSpec into a SignedAttestation:

    spec → sandbox.execute → ExecutionResult → build_signed_attestation → SignedAttestation

This is what the HTTP server, the CLI, and the MCP tools all call.
"""

from __future__ import annotations

import uuid

from dac.attestation import build_signed_attestation
from dac.sandbox import DockerSandbox, SandboxResult
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

        sandbox_result: SandboxResult = self._sandbox.execute(
            code=spec.code,
            language=spec.language,
            timeout_sec=spec.timeout_sec,
            memory_mb=spec.memory_mb,
        )

        execution_result = ExecutionResult(
            workload_id=workload_id,
            status=sandbox_result.status,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            exit_code=sandbox_result.exit_code,
            started_at=sandbox_result.started_at,
            ended_at=sandbox_result.ended_at,
            wall_time_sec=sandbox_result.wall_time_sec,
            cost_usd=self._compute_cost(sandbox_result),
            substrate_id=sandbox_result.substrate_id,
            output_hash=sandbox_result.output_hash,
            error=sandbox_result.error,
        )

        return build_signed_attestation(spec, execution_result, self._signer)

    @staticmethod
    def _compute_cost(result: SandboxResult) -> float:
        """Simple wall-time cost model for v0.

        Local Docker is essentially free, but we charge a notional rate
        so the cost field is non-zero and meaningful for downstream
        budgeting. Production substrates will have real rate cards.

        Rate: $0.0001 per wall-time-second (six orders of magnitude cheaper
        than AWS Lambda; this is a placeholder).
        """
        rate_per_sec = 0.0001
        return round(result.wall_time_sec * rate_per_sec, 8)
