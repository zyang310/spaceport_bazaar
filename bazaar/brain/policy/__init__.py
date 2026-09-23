"""Policies: the scripted exercise, and the agents that actually decide."""

from .base import Policy, ScriptedDriver
from .hustler import HustlerPolicy
from .scrooge import ScroogePolicy
from .utility import UtilityPolicy

#: The deciding policies, by the name the CLI knows them by.
AGENTS = {
    "utility": UtilityPolicy,
    "scrooge": ScroogePolicy,
    "hustler": HustlerPolicy,
}

__all__ = [
    "AGENTS",
    "HustlerPolicy",
    "Policy",
    "ScriptedDriver",
    "ScroogePolicy",
    "UtilityPolicy",
]
