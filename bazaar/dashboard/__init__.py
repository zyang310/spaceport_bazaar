"""A live page showing what the client sees and does, served beside every run."""

from .hub import Dashboard
from .server import DashboardServer

__all__ = ["Dashboard", "DashboardServer"]
