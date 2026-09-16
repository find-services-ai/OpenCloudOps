"""CloudOps agentic-AI repository bootstrap.

Programmatic use:

    from bootstrap import PlatformConfig, plan, write
    config = PlatformConfig.from_yaml("platform.yml")
    write(config, "./my-repo")
"""

from bootstrap.config import ConfigError, Environment, MeshPlane, PROFILES, PlatformConfig
from bootstrap.graph import generate_graph, generate_mesh_manifest
from bootstrap.scaffold import conflicts, plan, write

__all__ = [
    "ConfigError",
    "Environment",
    "MeshPlane",
    "PROFILES",
    "PlatformConfig",
    "conflicts",
    "generate_graph",
    "generate_mesh_manifest",
    "plan",
    "write",
]
