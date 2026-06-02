"""A2A (Agent-to-Agent) protocol support for Darwin Agentic Cloud."""

from darwin.agenticcloud.a2a.agent_card import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
)
from darwin.agenticcloud.a2a.skills import SKILLS, get_agent_card

__all__ = [
    "SKILLS",
    "AgentCapabilities",
    "AgentCard",
    "AgentProvider",
    "AgentSkill",
    "get_agent_card",
]
