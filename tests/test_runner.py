"""The utility loop: its time limit, and the request IDs it hands out.

A fake client stands in for the socket here -- these tests are about the
deadline logic in ``run_utility``, not the connection itself.  Plain
``asyncio.run`` is used rather than a pytest-asyncio plugin, since this is the
only place in the suite that needs one.
"""

import asyncio

import pytest

from bazaar import actions
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, run_utility
from bazaar.validation import limits, model

from .factories import result, state


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
                    session="s1",
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
                session="s1",
            ),
            timeout=1.0,
        )

    outcome = asyncio.run(scenario())
    assert outcome.ok


def test_the_cli_default_is_no_limit():
    """Regression guard: removing --max-seconds must mean unlimited, not 30."""
    from bazaar.__main__ import parse_args

    assert parse_args(["--policy", "utility"]).max_seconds is None


class RecordingClient(FakeClient):
    """Answers every command with OK and remembers the request IDs it saw."""

    def __init__(self, fixed_state):
        super().__init__(fixed_state)
        self.request_ids = []

    async def request(self, message, request_id, timeout=10):
        self.request_ids.append(request_id)
        return result(request_id, processed_version=self.state.world_version)

    async def wait_for_version(self, world_version, timeout=10):
        return self.state


class AdvertiseTwicePolicy:
    def decide(self, decided_state):
        return [
            actions.Advertise(
                selling=(model.Resource.RESOURCE_WATER,), expires_tick=decided_state.tick + 3
            )
            for _ in range(2)
        ]


def session_request_ids(session, tmp_path):
    client = RecordingClient(state())
    asyncio.run(
        run_utility(
            client, AdvertiseTwicePolicy(), RunLog(tmp_path), RunOutcome(), 0.1, session=session
        )
    )
    return client.request_ids


def test_a_restarted_client_never_reuses_a_request_id_from_the_same_run(tmp_path):
    """Regression: each session counted from utility-0001, so a restart mid-game
    had every command refused as RESULT_CODE_REQUEST_ID_CONFLICT."""
    first = session_request_ids("20260923-141017", tmp_path / "a")
    second = session_request_ids("20260923-141551", tmp_path / "b")

    assert len(first) == len(second) == 2
    assert not set(first) & set(second)
    for request_id in first + second:
        limits.check_request_id(request_id)
