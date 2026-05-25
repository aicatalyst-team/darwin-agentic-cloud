"""DAC MCP server.

Exposes DAC as an MCP (Model Context Protocol) server over stdio so
Claude Desktop, Cursor, and other MCP clients can call DAC as a tool.

Registered tools:
    dac_run_python              — execute Python code, return signed attestation
    dac_run_node                — execute Node.js code, return signed attestation
    dac_verify_attestation      — verify a signed attestation
    dac_identity                — show this DAC instance's public key and substrate id
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import dac
from dac.attestation import verify_attestation
from dac.runtime import Runtime
from dac.sandbox import SUBSTRATE_ID

# TODO(v0.2): per-role signing keys (cli vs http vs mcp) derived via HKDF.
# For v0, MCP uses the same Signer as CLI and HTTP — same trust scope.
_runtime = Runtime()
_server = Server("dac")


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
                "verification."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code to execute."},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600, "default": 30},
                    "memory_mb": {"type": "integer", "minimum": 64, "maximum": 8192, "default": 512},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="dac_run_node",
            description=(
                "Execute Node.js code in an isolated Docker sandbox and return a "
                "cryptographically signed attestation of the execution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "JavaScript/Node.js source code."},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 600, "default": 30},
                    "memory_mb": {"type": "integer", "minimum": 64, "maximum": 8192, "default": 512},
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
                    "attestation": {"type": "object", "description": "The attestation payload."},
                    "signature_b64": {"type": "string", "description": "Base64 Ed25519 signature."},
                    "public_key_b64": {"type": "string", "description": "Base64 Ed25519 public key."},
                },
                "required": ["attestation", "signature_b64", "public_key_b64"],
            },
        ),
        Tool(
            name="dac_identity",
            description=(
                "Return this DAC instance's identity: public key, key ID, "
                "substrate ID, schema version, and DAC version."
            ),
            inputSchema={"type": "object", "properties": {}},
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
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_run(arguments: dict[str, Any], language: str) -> list[TextContent]:
    from dac.types import WorkloadSpec

    spec = WorkloadSpec(
        code=arguments["code"],
        language=language,
        timeout_sec=int(arguments.get("timeout_sec", 30)),
        memory_mb=int(arguments.get("memory_mb", 512)),
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


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await _server.run(read_stream, write_stream, _server.create_initialization_options())


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
