"""Policies: the scripted exercise, and the agents that actually decide."""

from .base import Policy, ScriptedDriver
from .scrooge import ScroogePolicy
from .utility import UtilityPolicy

#: The deciding policies, by the name the CLI knows them by.
AGENTS = {
    "utility": UtilityPolicy,
    "scrooge": ScroogePolicy,
}

__all__ = ["AGENTS", "Policy", "ScriptedDriver", "ScroogePolicy", "UtilityPolicy"]
