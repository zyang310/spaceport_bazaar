"""What a policy is.

Two shapes live behind this module, because the two policies answer different
questions.

:class:`Policy` is the real one: a pure function from a state to a list of
actions.  Purity is what makes it testable, since a state captured from a live
run can be replayed offline and must produce the same decision.

:class:`ScriptedDriver` is the exception.  The guide's exercise is a fixed
sequence where each step waits for a specific set of responses -- including two
steps that send nothing at all -- so it has to see results and protocol errors,
not just states.  It drives the connection directly instead of returning
actions.  Only the utility policy needs to be a pure function of state.
"""

from typing import Protocol, runtime_checkable

from ...validation import model


@runtime_checkable
class Policy(Protocol):
    """Decides what to do, given only what the server reported."""

    name: str

    def decide(self, state: model.State) -> list:
        """Return the actions to take for this state.

        Must be a pure function of ``state`` plus the policy's own config, so
        the same state always produces the same actions.
        """
        ...


class ScriptedDriver(Protocol):
    """Runs a fixed sequence against a live connection."""

    name: str

    async def run(self, client) -> None:
        ...
