"""A2A skill definitions for Darwin Agentic Cloud.

Defines the three core skills exposed via the A2A protocol:
1. darwin.run — execute a workload, return a signed attestation
2. darwin.verify — verify an attestation signature
3. darwin.identity — return signer identity and substrate info
"""

from __future__ import annotations

from darwin.agenticcloud.a2a.agent_card import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
)

SKILL_RUN = AgentSkill(
    id="darwin.run",
    name="Run Workload",
    description=(
        "Execute a Python or Node.js workload in an isolated sandbox and return "
        "a cryptographically signed Ed25519 attestation proving the execution occurred."
    ),
    tags=["compute", "attestation", "sandbox", "verifiable-execution"],
    inputModes=["application/json"],
    outputModes=["application/json"],
    examples=[
        {
            "input": {
                "code": "print('hello world')",
                "language": "python",
                "timeout_sec": 30,
                "memory_mb": 512,
                "cost_cap_usd": 0.01,
            },
            "output": {
                "attestation": {
                    "attestation_id": "a1b2c3d4-...",
                    "status": "ok",
                    "stdout": "hello world\n",
                    "signer_key_id": "dac-local-abcdef1234567890",
                },
                "signature_b64": "<base64-ed25519-signature>",
                "public_key_b64": "<base64-ed25519-public-key>",
            },
        },
    ],
)

SKILL_VERIFY = AgentSkill(
    id="darwin.verify",
    name="Verify Attestation",
    description=(
        "Verify an Ed25519 signature on a Darwin attestation. Returns whether "
        "the signature is valid for the given payload under the specified public key."
    ),
    tags=["verification", "cryptography", "attestation"],
    inputModes=["application/json"],
    outputModes=["application/json"],
    examples=[
        {
            "input": {
                "attestation": {"attestation_id": "a1b2c3d4-...", "status": "ok"},
                "signature_b64": "<base64-ed25519-signature>",
                "public_key_b64": "<base64-ed25519-public-key>",
            },
            "output": {
                "verified": True,
                "attestation_id": "a1b2c3d4-...",
                "signer_key_id": "dac-local-abcdef1234567890",
            },
        },
    ],
)

SKILL_IDENTITY = AgentSkill(
    id="darwin.identity",
    name="Get Identity",
    description=(
        "Return the signer identity, public key, substrate information, and "
        "version of this Darwin Agentic Cloud instance."
    ),
    tags=["identity", "metadata", "discovery"],
    inputModes=["application/json"],
    outputModes=["application/json"],
    examples=[
        {
            "input": {},
            "output": {
                "key_id": "dac-local-abcdef1234567890",
                "public_key_b64": "<base64-ed25519-public-key>",
                "substrate_id": "local-docker-v0",
                "schema": "darwin.cloud/agenticcloud/attestation/v0.1",
                "version": "2.0.0",
            },
        },
    ],
)

SKILLS: list[AgentSkill] = [SKILL_RUN, SKILL_VERIFY, SKILL_IDENTITY]


def get_agent_card(
    *,
    url: str = "https://darwin-agentic-cloud.fly.dev",
    version: str | None = None,
) -> AgentCard:
    """Build the full A2A Agent Card for Darwin Agentic Cloud.

    Parameters
    ----------
    url:
        The base URL where the agent is reachable.
    version:
        Override version string. Defaults to darwin.agenticcloud.__version__.
    """
    from darwin import agenticcloud as dac

    return AgentCard(
        name="Darwin Agentic Cloud",
        description=(
            "Verifiable compute substrate for AI agents. Every skill invocation "
            "produces a cryptographically signed Ed25519 attestation. MCP-native "
            "and A2A-discoverable. Open source (Apache 2.0)."
        ),
        url=url,
        provider=AgentProvider(
            organization="Darwin",
            url="https://github.com/vje013/darwin-agentic-cloud",
        ),
        version=version or dac.__version__,
        documentationUrl="https://github.com/vje013/darwin-agentic-cloud#readme",
        capabilities=AgentCapabilities(
            streaming=False,
            pushNotifications=False,
            stateTransitionHistory=False,
        ),
        authentication={"schemes": [], "credentials": None},
        defaultInputModes=["application/json"],
        defaultOutputModes=["application/json"],
        skills=SKILLS,
    )
