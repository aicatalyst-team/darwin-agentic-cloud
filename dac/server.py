"""DAC HTTP server (FastAPI).

Exposes the runtime over HTTP so any client — curl, a remote agent,
another service — can request signed execution.

Endpoints:
    GET  /healthz                      — liveness
    GET  /v0/identity                  — server's public key + substrate id
    POST /v0/run                       — execute workload, return signed attestation
    POST /v0/attestations/verify       — verify a signed attestation
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import dac
from dac.attestation import verify_attestation
from dac.runtime import Runtime
from dac.sandbox import SUBSTRATE_ID
from dac.signing import Signer
from dac.types import WorkloadSpec


# -------------------------------------------------------------------
# Request / response schemas
# -------------------------------------------------------------------
class RunRequest(BaseModel):
    code: str = Field(..., description="Source code to execute.")
    language: str = Field("python", description="Language: 'python' or 'node'.")
    inputs: dict = Field(default_factory=dict, description="Optional inputs passed to the workload.")
    cost_cap_usd: float = Field(0.01, ge=0, description="Cost ceiling in USD.")
    timeout_sec: int = Field(30, ge=1, le=600, description="Wall-time timeout in seconds.")
    memory_mb: int = Field(512, ge=64, le=8192, description="Memory limit in MB.")


class RunResponse(BaseModel):
    attestation: dict
    signature_b64: str
    public_key_b64: str


class VerifyRequest(BaseModel):
    attestation: dict
    signature_b64: str
    public_key_b64: str


class VerifyResponse(BaseModel):
    verified: bool


class IdentityResponse(BaseModel):
    key_id: str
    public_key_b64: str
    substrate_id: str
    schema: str
    version: str


# -------------------------------------------------------------------
# App factory
# -------------------------------------------------------------------
def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Build a FastAPI app with the given runtime (or a default one)."""
    rt = runtime or Runtime()

    app = FastAPI(
        title="Darwinic Agentic Cloud",
        description="Verifiable compute for AI agents with cryptographically signed attestations.",
        version=dac.__version__,
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v0/identity", response_model=IdentityResponse, tags=["identity"])
    async def identity() -> IdentityResponse:
        signer: Signer = rt.signer
        return IdentityResponse(
            key_id=signer.key_id(),
            public_key_b64=signer.public_key_b64(),
            substrate_id=SUBSTRATE_ID,
            schema=dac.ATTESTATION_SCHEMA,
            version=dac.__version__,
        )

    @app.post("/v0/run", response_model=RunResponse, tags=["run"])
    async def run(req: RunRequest) -> RunResponse:
        spec = WorkloadSpec(
            code=req.code,
            language=req.language,
            inputs=req.inputs,
            cost_cap_usd=req.cost_cap_usd,
            timeout_sec=req.timeout_sec,
            memory_mb=req.memory_mb,
        )
        # Sandbox is blocking; run it on a worker thread so the event loop stays free.
        signed = await asyncio.to_thread(rt.run, spec)
        return RunResponse(
            attestation=signed.attestation,
            signature_b64=signed.signature_b64,
            public_key_b64=signed.public_key_b64,
        )

    @app.post("/v0/attestations/verify", response_model=VerifyResponse, tags=["attestations"])
    async def verify(req: VerifyRequest) -> VerifyResponse:
        try:
            payload: dict[str, Any] = {
                "attestation": req.attestation,
                "signature_b64": req.signature_b64,
                "public_key_b64": req.public_key_b64,
            }
            return VerifyResponse(verified=verify_attestation(payload))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(e)) from e

    return app


app = create_app()
