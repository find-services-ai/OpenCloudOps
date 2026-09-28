"""Registry of agent adapters.

Ported from ``bootstrap/agent_adapters/__init__.py``. Same five names, same
lookup shape -- ``get_adapter(name)`` -- so anything that learned the
original framework's adapter names needs no relearning here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opencloudops.bootstrap.adapters._base import AgentAdapter

ADAPTER_NAMES = ["copilot", "cursor", "claude-code", "continue", "agnostic"]


def get_adapter(agent: str) -> "AgentAdapter":
    agent = agent.lower()
    if agent == "copilot":
        from opencloudops.bootstrap.adapters.copilot import CopilotAdapter

        return CopilotAdapter()
    if agent == "cursor":
        from opencloudops.bootstrap.adapters.cursor import CursorAdapter

        return CursorAdapter()
    if agent in ("claude-code", "claude_code"):
        from opencloudops.bootstrap.adapters.claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter()
    if agent in ("continue", "continue_dev"):
        from opencloudops.bootstrap.adapters.continue_dev import ContinueDevAdapter

        return ContinueDevAdapter()
    if agent == "agnostic":
        from opencloudops.bootstrap.adapters._base import AgnosticAdapter

        return AgnosticAdapter()
    raise ValueError(f"Unknown agent: {agent!r}. Valid choices: {', '.join(ADAPTER_NAMES)}")
