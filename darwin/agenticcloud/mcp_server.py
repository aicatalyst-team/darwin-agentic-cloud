"""Darwin MCP server.

Exposes Darwin as an MCP server over stdio so Claude Desktop, Cursor,
and other MCP clients can call Darwin as a tool.

Tools (v3.0.0 surface — matches CLI verbs):

    darwin_run         — execute workload, return signed v0.2 attestation
    darwin_verify      — verify a v0.2 attestation against the live keylist
    darwin_price       — preflight only, return cost quote per substrate
    darwin_list        — list discoverable substrates
    darwin_who         — show whose key signed an attestation
    darwin_history     — list recent attestations
    darwin_stats       — aggregate stats across stored attestations
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from darwin import run as _darwin_run
from darwin.agenticcloud.hashing import canonical_json
from darwin.agenticcloud.router import discover_substrates, resolve_short_name
from darwin.agenticcloud.signing import verify_signature
from darwin.agenticcloud.storage import AttestationStore
from darwin.agenticcloud.substrate.base import (
    PreflightRejected,
    build_identity_payload,
)
from darwin.agenticcloud.types import WorkloadSpec

_server = Server("darwin-agenticcloud")
_store = AttestationStore()

KEYLIST_URL = "https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json"


@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="darwin_run",
            description=(
                "Execute a workload (code) on a Darwin substrate and return a "
                "cryptographically signed v0.2 attestation. The attestation binds "
                "the workload spec, output, substrate identity, cost, and routing "
                "decision. Substrate is auto-picked (cheapest available) unless "
                "overridden. Cost cap is enforced pre-execution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Workload source code."},
                    "language": {
                        "type": "string",
                        "enum": ["python", "node"],
                        "default": "python",
                    },
                    "substrate": {
                        "type": "string",
                        "description": (
                            "Short name (local, aws-batch, aws-lambda, modal) or "
                            "full substrate ID. Omit for auto-routing."
                        ),
                    },
                    "cost_cap_usd": {"type": "number", "minimum": 0.0001, "default": 0.10},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600, "default": 30},
                    "memory_mb": {
                        "type": "integer",
                        "minimum": 64,
                        "maximum": 8192,
                        "default": 512,
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="darwin_verify",
            description=(
                "Verify a v0.2 attestation cryptographically. Fetches the public "
                "keylist, looks up the substrate identity key, and verifies the "
                "signature against the published key."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "attestation": {
                        "type": "object",
                        "description": "Full v0.2 attestation dict (the output of darwin_run).",
                    },
                    "keylist_url": {
                        "type": "string",
                        "description": "Override keylist URL.",
                        "default": KEYLIST_URL,
                    },
                },
                "required": ["attestation"],
            },
        ),
        Tool(
            name="darwin_price",
            description=(
                "Get a cost quote without running the workload. Returns a list "
                "of available substrates sorted by estimated cost."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "language": {"type": "string", "default": "python"},
                    "cost_cap_usd": {"type": "number", "default": 0.10},
                    "timeout_sec": {"type": "integer", "default": 30},
                    "memory_mb": {"type": "integer", "default": 512},
                    "substrate": {
                        "type": "string",
                        "description": "Optional — restrict pricing to one substrate.",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="darwin_list",
            description="List substrates this environment can use right now.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="darwin_who",
            description=(
                "Show whose keys signed an attestation. Lighter than darwin_verify "
                "— does NOT run cryptographic verification, just key-id lookup."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "attestation": {"type": "object"},
                    "keylist_url": {"type": "string", "default": KEYLIST_URL},
                },
                "required": ["attestation"],
            },
        ),
        Tool(
            name="darwin_history",
            description="List recent attestations from local storage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
            },
        ),
        Tool(
            name="darwin_stats",
            description="Aggregate stats across stored attestations.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    handlers = {
        "darwin_run": _handle_run,
        "darwin_verify": _handle_verify,
        "darwin_price": _handle_price,
        "darwin_list": _handle_list,
        "darwin_who": _handle_who,
        "darwin_history": _handle_history,
        "darwin_stats": _handle_stats,
    }
    handler = handlers.get(name)
    if handler is None:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    if asyncio.iscoroutinefunction(handler):
        return await handler(arguments)  # type: ignore[no-any-return]
    return handler(arguments)  # type: ignore[return-value]


async def _handle_run(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        attestation = await asyncio.to_thread(
            _darwin_run,
            arguments["code"],
            substrate=arguments.get("substrate"),
            language=arguments.get("language", "python"),
            cost_cap=float(arguments.get("cost_cap_usd", 0.10)),
            timeout=int(arguments.get("timeout_sec", 30)),
            memory_mb=int(arguments.get("memory_mb", 512)),
        )
        return [TextContent(type="text", text=json.dumps(attestation, indent=2))]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": str(e), "error_type": type(e).__name__}, indent=2),
            )
        ]


def _handle_verify(arguments: dict[str, Any]) -> list[TextContent]:
    import urllib.request

    attestation = arguments["attestation"]
    keylist_url = arguments.get("keylist_url", KEYLIST_URL)
    try:
        with urllib.request.urlopen(keylist_url, timeout=10) as resp:
            keylist_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=json.dumps({"verified": False, "error": f"keylist fetch failed: {e}"}),
            )
        ]

    substrate_block = attestation.get("execution_result", {}).get("substrate", {})
    target_id = substrate_block.get("identity_signer_key_id")
    if not target_id:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"verified": False, "error": "no identity_signer_key_id in attestation"}
                ),
            )
        ]

    matching = next(
        (k for k in keylist_data.get("keys", []) if k.get("signer_key_id") == target_id), None
    )
    if matching is None:
        return [
            TextContent(
                type="text",
                text=json.dumps({"verified": False, "error": f"key {target_id} not in keylist"}),
            )
        ]

    payload = build_identity_payload(
        substrate_id=substrate_block.get("id", ""),
        substrate_version=substrate_block.get("version", ""),
        workload_spec_hash=attestation.get("workload_spec_hash", ""),
        output_hash=attestation.get("execution_result", {}).get("output_hash", ""),
        evidence_schema_id=substrate_block.get("evidence_schema_id", ""),
        issued_at=attestation.get("issued_at", ""),
    )
    canonical = canonical_json(payload)
    verified = verify_signature(
        canonical,
        substrate_block.get("identity_signature", ""),
        matching.get("public_key_b64", ""),
    )
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "verified": verified,
                    "signer_key_id": target_id,
                    "signer_status": matching.get("status", "unknown"),
                    "attestation_id": attestation.get("attestation_id"),
                },
                indent=2,
            ),
        )
    ]


async def _handle_price(arguments: dict[str, Any]) -> list[TextContent]:
    spec = WorkloadSpec(
        code=arguments["code"],
        language=arguments.get("language", "python"),
        cost_cap_usd=float(arguments.get("cost_cap_usd", 0.10)),
        timeout_sec=int(arguments.get("timeout_sec", 30)),
        memory_mb=int(arguments.get("memory_mb", 512)),
    )
    subs = await asyncio.to_thread(discover_substrates)
    if arguments.get("substrate"):
        try:
            subs = [resolve_short_name(arguments["substrate"], subs)]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    quotes = []
    for sub in subs:
        try:
            est = await asyncio.to_thread(sub.preflight, spec)
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": float(est.cost_usd_max),
                    "status": "ok",
                }
            )
        except PreflightRejected as e:
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": None,
                    "status": "rejected",
                    "reason": str(e),
                }
            )
        except Exception as e:
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": None,
                    "status": "error",
                    "reason": f"{type(e).__name__}: {e}",
                }
            )
    quotes.sort(key=lambda q: (q["cost_usd_max"] is None, q["cost_usd_max"] or 0.0))
    return [TextContent(type="text", text=json.dumps(quotes, indent=2))]


async def _handle_list(arguments: dict[str, Any]) -> list[TextContent]:
    subs = await asyncio.to_thread(discover_substrates)
    out = [{"substrate_id": s.substrate_id, "substrate_version": s.substrate_version} for s in subs]
    return [TextContent(type="text", text=json.dumps(out, indent=2))]


def _handle_who(arguments: dict[str, Any]) -> list[TextContent]:
    import urllib.request

    att = arguments["attestation"]
    keylist_url = arguments.get("keylist_url", KEYLIST_URL)
    try:
        with urllib.request.urlopen(keylist_url, timeout=10) as resp:
            keylist_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": f"keylist fetch failed: {e}"}))]

    sub_block = att.get("execution_result", {}).get("substrate", {})
    sub_id = sub_block.get("identity_signer_key_id", "?")
    outer_id = att.get("signer_key_id", "?")
    keys = {k.get("signer_key_id"): k for k in keylist_data.get("keys", [])}
    sub_key = keys.get(sub_id)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "attestation_id": att.get("attestation_id"),
                    "substrate_signer": {
                        "signer_key_id": sub_id,
                        "in_keylist": sub_key is not None,
                        "status": sub_key.get("status") if sub_key else "unknown",
                    },
                    "outer_signer": {
                        "signer_key_id": outer_id,
                        "note": "operator-local key; not anchored to public keylist by design",
                    },
                },
                indent=2,
            ),
        )
    ]


def _handle_history(arguments: dict[str, Any]) -> list[TextContent]:
    limit = int(arguments.get("limit", 10))
    rows = _store.list_recent(limit=limit)
    return [TextContent(type="text", text=json.dumps(rows, indent=2))]


def _handle_stats(arguments: dict[str, Any]) -> list[TextContent]:
    stats = {"count": _store.count(), "total_cost_usd": _store.total_cost_usd()}
    return [TextContent(type="text", text=json.dumps(stats, indent=2))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await _server.run(
            read_stream,
            write_stream,
            _server.create_initialization_options(),
        )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
