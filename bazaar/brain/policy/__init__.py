"""Policies: the scripted exercise, and the agents that actually decide."""

from .base import Policy, ScriptedDriver
from .jesus import JesusPolicy
from .scrooge import ScroogePolicy
from .utility import UtilityPolicy

#: The deciding policies, by the name the CLI knows them by.
AGENTS = {
    "utility": UtilityPolicy,
    "scrooge": ScroogePolicy,
    "jesus": JesusPolicy,
}

__all__ = [
    "AGENTS",
    "JesusPolicy",
    "Policy",
    "ScriptedDriver",
    "ScroogePolicy",
    "UtilityPolicy",
]
