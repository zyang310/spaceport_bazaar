"""The utility loop's time limit: unlimited by default, bounded on request.

A fake client stands in for the socket here -- these tests are about the
deadline logic in ``run_utility``, not the connection itself.  Plain
``asyncio.run`` is used rather than a pytest-asyncio plugin, since this is the
only place in the suite that needs one.
"""

import asyncio

import pytest

from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, run_utility

from .factories import state


class FakeClient:
    """Enough of BazaarClient's interface for run_utility to drive.

    The state never changes and is never PHASE_RUNNING, so the policy always
    decides nothing -- run_utility's only way to stop is its own deadline.
    """

    def __init__(self, fixed_state):
        self.state = fixed_state
        self.sent = 0

    async def first_state(self):
        return self.state

    async def declare_ready(self, snapshot_sequence, ready=True, timeout=10):
        return object()

    async def quiet(self, seconds=0.4):
        await asyncio.sleep(min(seconds, 0.02))  # keep the test fast
        return None


class DoNothingPolicy:
    def decide(self, decided_state):
        return []


def test_unlimited_by_default_does_not_stop_on_its_own(tmp_path):
    """``max_seconds=None`` must outlast a generous outer timeout."""

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                run_utility(
                    FakeClient(state()),
                    DoNothingPolicy(),
                    RunLog(tmp_path),
                    RunOutcome(),
                    None,
                ),
                timeout=0.3,
            )

    asyncio.run(scenario())


def test_a_bounded_run_ends_on_its_own(tmp_path):
    """A small ``max_seconds`` returns well inside a generous outer timeout."""

    async def scenario():
        return await asyncio.wait_for(
            run_utility(
                FakeClient(state()),
                DoNothingPolicy(),
                RunLog(tmp_path),
                RunOutcome(),
                0.05,
            ),
            timeout=1.0,
        )

    outcome = asyncio.run(scenario())
    assert outcome.ok


def test_the_cli_default_is_no_limit():
    """Regression guard: removing --max-seconds must mean unlimited, not 30."""
    from bazaar.__main__ import parse_args

    assert parse_args(["--policy", "utility"]).max_seconds is None
