"""ChatOps: the demo surface, and the way the framework is actually used.

One text command in, one markdown reply out. The parser is deterministic --
no model sits between what someone typed and what the framework does. That
matters most at the approval step: a chat message that grants a MEDIUM-risk
action must mean exactly what it says, every time, or the autonomy ladder is
decoration.
"""

from opencloudops.chatops.commands import ChatOps, ChatReply, Command, parse  # noqa: F401

__all__ = ["ChatOps", "ChatReply", "Command", "parse"]
