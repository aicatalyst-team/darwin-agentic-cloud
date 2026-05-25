"""A2A Agent Card Pydantic model.

Matches the Google A2A protocol Agent Card schema (v0.3.0+).
Reference: https://github.com/google-a2a/A2A

The Agent Card is served at /.well-known/agent-card.json and describes
the agent's identity, capabilities, and skills to other A2A clients.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentProvider(BaseModel):
    """Organization or entity that provides the agent."""

    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    """Capabilities supported by the agent."""

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False


class AgentSkill(BaseModel):
    """A single skill that the agent can perform."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    inputModes: list[str] = Field(default_factory=list)
    outputModes: list[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """A2A Agent Card (v0.3.0+).

    Describes an agent's identity, endpoint, capabilities, and skills
    so that other A2A-compatible clients can discover and invoke it.
    """

    name: str
    description: str
    url: str
    provider: AgentProvider | None = None
    version: str
    documentationUrl: str | None = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    authentication: dict[str, Any] | None = None
    defaultInputModes: list[str] = Field(default_factory=lambda: ["application/json"])
    defaultOutputModes: list[str] = Field(default_factory=lambda: ["application/json"])
    skills: list[AgentSkill] = Field(default_factory=list)
