"""Integrations: turning things that already exist into resource graphs.

``ideas`` reads an application directory and derives a graph from what is
actually in it. ``ethos`` publishes this framework's capability descriptor so
the GOS harness can register and route to it.
"""

from opencloudops.integrations.ideas import discover, discover_all  # noqa: F401

__all__ = ["discover", "discover_all"]
