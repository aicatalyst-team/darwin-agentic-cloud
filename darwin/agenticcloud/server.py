"""DAC HTTP server (FastAPI).

Exposes the runtime over HTTP so any client — curl, a remote agent,
another service — can request signed execution and query history.

Endpoints:
    GET  /healthz                          — liveness
    GET  /v0/identity                      — server's public key + substrate id
    POST /v0/run                           — execute workload, return signed attestation
    POST /v0/attestations/verify           — verify a signed attestation
    GET  /v0/attestations                  — list recent attestations
    GET  /v0/attestations/{id}             — fetch a specific attestation
    GET  /v0/attestations/stats            — aggregate stats
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from darwin import agenticcloud as dac
from darwin.agenticcloud.attestation import verify_attestation
from darwin.agenticcloud.runtime import Runtime
from darwin.agenticcloud.sandbox import SUBSTRATE_ID
from darwin.agenticcloud.signing import Signer
from darwin.agenticcloud.storage import AttestationStore
from darwin.agenticcloud.types import WorkloadSpec


# -------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------
class RunRequest(BaseModel):
    code: str = Field(..., description="Source code to execute.")
    language: str = Field("python", description="Language: 'python' or 'node'.")
    inputs: dict = Field(default_factory=dict, description="Optional inputs.")
    cost_cap_usd: float = Field(0.01, ge=0, description="Cost ceiling in USD.")
    timeout_sec: int = Field(30, ge=1, le=600, description="Timeout in seconds.")
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


class AttestationSummary(BaseModel):
    attestation_id: str
    workload_id: str
    signer_key_id: str
    substrate_id: str
    status: str
    issued_at: float
    cost_usd: float
    wall_time_sec: float
    schema_version: str


class AttestationListResponse(BaseModel):
    attestations: list[AttestationSummary]
    count: int


class AttestationStatsResponse(BaseModel):
    total_count: int
    total_cost_usd: float
    by_status: dict[str, int]


# -------------------------------------------------------------------
# App factory
# -------------------------------------------------------------------
def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Build a FastAPI app with the given runtime (or a default one)."""
    rt = runtime or Runtime()
    store: AttestationStore = rt.store

    app = FastAPI(
        title="Darwin Agentic Cloud",
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
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get(
        "/v0/attestations/stats",
        response_model=AttestationStatsResponse,
        tags=["attestations"],
    )
    async def stats() -> AttestationStatsResponse:
        total = store.count()
        total_cost = store.total_cost_usd()
        by_status = {
            "ok": len(store.list_by_status("ok", limit=10**9)),
            "error": len(store.list_by_status("error", limit=10**9)),
            "timeout": len(store.list_by_status("timeout", limit=10**9)),
            "oom": len(store.list_by_status("oom", limit=10**9)),
            "cost_exceeded": len(store.list_by_status("cost_exceeded", limit=10**9)),
        }
        return AttestationStatsResponse(
            total_count=total,
            total_cost_usd=total_cost,
            by_status=by_status,
        )

    @app.get(
        "/v0/attestations",
        response_model=AttestationListResponse,
        tags=["attestations"],
    )
    async def list_attestations(
        limit: int = Query(20, ge=1, le=1000),
        status: str | None = Query(None),
    ) -> AttestationListResponse:
        rows = (
            store.list_by_status(status, limit=limit) if status else store.list_recent(limit=limit)
        )
        return AttestationListResponse(
            attestations=[
                AttestationSummary(
                    attestation_id=r.attestation_id,
                    workload_id=r.workload_id,
                    signer_key_id=r.signer_key_id,
                    substrate_id=r.substrate_id,
                    status=r.status,
                    issued_at=r.issued_at,
                    cost_usd=r.cost_usd,
                    wall_time_sec=r.wall_time_sec,
                    schema_version=r.schema_version,
                )
                for r in rows
            ],
            count=len(rows),
        )

    @app.get("/v0/attestations/{attestation_id}", tags=["attestations"])
    async def get_attestation(attestation_id: str) -> dict:
        fetched = store.get(attestation_id)
        if fetched is None:
            raise HTTPException(status_code=404, detail="Attestation not found")
        return fetched.signed_attestation

    return app


app = create_app()
