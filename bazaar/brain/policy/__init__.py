"""Policies: the scripted exercise, and the agents that actually decide."""

from .base import Policy, ScriptedDriver
from .hivemind import HivemindPolicy
from .hustler import HustlerPolicy
from .scrooge import ScroogePolicy
from .utility import UtilityPolicy

#: The deciding policies, by the name the CLI knows them by.
AGENTS = {
    "utility": UtilityPolicy,
    "scrooge": ScroogePolicy,
    "hustler": HustlerPolicy,
    "hivemind": HivemindPolicy,
}

__all__ = [
    "AGENTS",
    "HivemindPolicy",
    "HustlerPolicy",
    "Policy",
    "ScriptedDriver",
    "ScroogePolicy",
    "UtilityPolicy",
]
