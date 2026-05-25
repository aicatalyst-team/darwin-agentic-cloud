"""Core data types for DAC."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkloadSpec:
    """What an agent asks to run."""

    code: str
    language: str = "python"
    inputs: dict = field(default_factory=dict)
    cost_cap_usd: float = 0.01
    timeout_sec: int = 30
    memory_mb: int = 512


@dataclass
class ExecutionResult:
    """What happened when the workload ran."""

    workload_id: str
    status: str  # "ok" | "error" | "timeout" | "oom" | "cost_exceeded"
    stdout: str
    stderr: str
    exit_code: int | None
    started_at: float
    ended_at: float
    wall_time_sec: float
    cost_usd: float
    substrate_id: str
    output_hash: str
    error: str | None = None


@dataclass
class Attestation:
    """The unsigned attestation payload."""

    schema: str
    attestation_id: str
    workload_spec_hash: str
    workload_spec: dict
    execution_result: dict
    signer_key_id: str
    issued_at: float


@dataclass
class SignedAttestation:
    """Signed attestation envelope."""

    attestation: dict
    signature_b64: str
    public_key_b64: str
