"""A local Bazaar game with bot traders, for testing the client and dashboard.

Run it with ``python -m bazaar.sandbox``; see ``__main__.py``.
"""

from .server import SandboxServer
from .world import World

__all__ = ["SandboxServer", "World"]
