"""DAC MCP server.

Exposes DAC as an MCP server over stdio so Claude Desktop, Cursor,
and other MCP clients can call DAC as a tool.

Tools:
    dac_run_python              — execute Python code, return signed attestation
    dac_run_node                — execute Node.js code, return signed attestation
    dac_verify_attestation      — verify a signed attestation
    dac_identity                — show this DAC instance's identity
    dac_history_recent          — list recent attestations
    dac_history_stats           — aggregate stats across stored attestations
    dac_history_get             — fetch a specific attestation by ID
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from darwin import agenticcloud as dac
from darwin.agenticcloud.attestation import verify_attestation
from darwin.agenticcloud.runtime import Runtime
from darwin.agenticcloud.sandbox import SUBSTRATE_ID

# TODO(v0.2): per-role signing keys (cli vs http vs mcp) via HKDF.
_runtime = Runtime()
_server = Server("darwin-agenticcloud")


@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="dac_run_python",
            description=(
                "Execute Python code in an isolated Docker sandbox and return a "
                "cryptographically signed attestation of the execution. The attestation "
                "binds the source code, the output, the substrate identity, the cost, "
                "and the signer's identity. Any tampering with any field breaks "
                "verification. Workloads whose maximum possible cost exceeds "
                "cost_cap_usd are rejected before the sandbox is launched; the "
                "rejection itself is signed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code."},
                    "timeout_sec": {
                        "type": "integer", "minimum": 1, "maximum": 600, "default": 30
                    },
                    "memory_mb": {
                        "type": "integer", "minimum": 64, "maximum": 8192, "default": 512
                    },
                    "cost_cap_usd": {
                        "type": "number", "minimum": 0.0001, "default": 0.01,
                        "description": "Maximum cost the workload may incur. Pre-flight enforced."
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="dac_run_node",
            description=(
                "Execute Node.js code in an isolated Docker sandbox and return a "
                "cryptographically signed attestation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "JS/Node source code."},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600, "default": 30},
                    "memory_mb": {"type": "integer", "minimum": 64, "maximum": 8192, "default": 512},
                    "cost_cap_usd": {"type": "number", "minimum": 0.0001, "default": 0.01},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="dac_verify_attestation",
            description=(
                "Verify a signed DAC attestation. Returns whether the signature is "
                "valid for the attestation payload under the embedded public key."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "attestation": {"type": "object", "description": "Attestation payload."},
                    "signature_b64": {"type": "string", "description": "Base64 Ed25519 signature."},
                    "public_key_b64": {"type": "string", "description": "Base64 Ed25519 public key."},
                },
                "required": ["attestation", "signature_b64", "public_key_b64"],
            },
        ),
        Tool(
            name="dac_identity",
            description="Return this DAC instance's identity.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="dac_history_recent",
            description=(
                "List the most recent attestations stored by this DAC instance. "
                "Useful for an agent to audit its own past execution. Returns a "
                "summary per attestation (id, workload_id, status, cost, timing, "
                "substrate)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer", "minimum": 1, "maximum": 200, "default": 20
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status (ok, error, timeout, oom, cost_exceeded).",
                    },
                },
            },
        ),
        Tool(
            name="dac_history_stats",
            description=(
                "Return aggregate stats across all attestations stored by this DAC "
                "instance: total count, total cost USD, count by status."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="dac_history_get",
            description=(
                "Fetch the full signed attestation for a given attestation ID. "
                "Accepts the full UUID. Returns the complete signed attestation "
                "(attestation, signature_b64, public_key_b64) which can be passed "
                "to dac_verify_attestation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "attestation_id": {"type": "string", "description": "Full attestation UUID."},
                },
                "required": ["attestation_id"],
            },
        ),
    ]


@_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "dac_run_python":
        return await _handle_run(arguments, language="python")
    if name == "dac_run_node":
        return await _handle_run(arguments, language="node")
    if name == "dac_verify_attestation":
        return _handle_verify(arguments)
    if name == "dac_identity":
        return _handle_identity()
    if name == "dac_history_recent":
        return _handle_history_recent(arguments)
    if name == "dac_history_stats":
        return _handle_history_stats()
    if name == "dac_history_get":
        return _handle_history_get(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_run(arguments: dict[str, Any], language: str) -> list[TextContent]:
    from darwin.agenticcloud.types import WorkloadSpec

    spec = WorkloadSpec(
        code=arguments["code"],
        language=language,
        timeout_sec=int(arguments.get("timeout_sec", 30)),
        memory_mb=int(arguments.get("memory_mb", 512)),
        cost_cap_usd=float(arguments.get("cost_cap_usd", 0.01)),
    )
    signed = await asyncio.to_thread(_runtime.run, spec)
    payload = {
        "attestation": signed.attestation,
        "signature_b64": signed.signature_b64,
        "public_key_b64": signed.public_key_b64,
    }
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def _handle_verify(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        ok = verify_attestation(
            {
                "attestation": arguments["attestation"],
                "signature_b64": arguments["signature_b64"],
                "public_key_b64": arguments["public_key_b64"],
            }
        )
        result = {
            "verified": ok,
            "attestation_id": arguments["attestation"].get("attestation_id"),
            "signer_key_id": arguments["attestation"].get("signer_key_id"),
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:  # noqa: BLE001
        return [TextContent(type="text", text=json.dumps({"verified": False, "error": str(e)}, indent=2))]


def _handle_identity() -> list[TextContent]:
    signer = _runtime.signer
    payload = {
        "key_id": signer.key_id(),
        "public_key_b64": signer.public_key_b64(),
        "substrate_id": SUBSTRATE_ID,
        "schema": dac.ATTESTATION_SCHEMA,
        "version": dac.__version__,
    }
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def _handle_history_recent(arguments: dict[str, Any]) -> list[TextContent]:
    limit = int(arguments.get("limit", 20))
    status = arguments.get("status")
    store = _runtime.store
    rows = store.list_by_status(status, limit=limit) if status else store.list_recent(limit=limit)
    summaries = [
        {
            "attestation_id": r.attestation_id,
            "workload_id": r.workload_id,
            "signer_key_id": r.signer_key_id,
            "substrate_id": r.substrate_id,
            "status": r.status,
            "issued_at": r.issued_at,
            "cost_usd": r.cost_usd,
            "wall_time_sec": r.wall_time_sec,
            "schema_version": r.schema_version,
        }
        for r in rows
    ]
    return [TextContent(type="text", text=json.dumps({"count": len(summaries), "attestations": summaries}, indent=2))]


def _handle_history_stats() -> list[TextContent]:
    store = _runtime.store
    payload = {
        "total_count": store.count(),
        "total_cost_usd": store.total_cost_usd(),
        "by_status": {
            "ok": len(store.list_by_status("ok", limit=10**9)),
            "error": len(store.list_by_status("error", limit=10**9)),
            "timeout": len(store.list_by_status("timeout", limit=10**9)),
            "oom": len(store.list_by_status("oom", limit=10**9)),
            "cost_exceeded": len(store.list_by_status("cost_exceeded", limit=10**9)),
        },
    }
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def _handle_history_get(arguments: dict[str, Any]) -> list[TextContent]:
    store = _runtime.store
    attestation_id = arguments["attestation_id"]
    fetched = store.get(attestation_id)
    if fetched is None:
        return [TextContent(type="text", text=json.dumps({"error": "not_found", "attestation_id": attestation_id}, indent=2))]
    return [TextContent(type="text", text=json.dumps(fetched.signed_attestation, indent=2))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await _server.run(read_stream, write_stream, _server.create_initialization_options())


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
