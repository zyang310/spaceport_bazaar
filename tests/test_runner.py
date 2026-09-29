"""The utility loop: its time limit, its request IDs, and stale decisions.

A fake client stands in for the socket here -- these tests are about the
deadline logic in ``run_utility``, not the connection itself.  Plain
``asyncio.run`` is used rather than a pytest-asyncio plugin, since this is the
only place in the suite that needs one.
"""

import asyncio
import dataclasses
import json

import pytest

from bazaar import actions
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, run_utility
from bazaar.validation import limits, model

from .factories import result, rules, state


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


class TickingClient(RecordingClient):
    """The tick moves on while the first command is in flight, as it does when
    a batch takes longer than a tick to send."""

    def __init__(self, fixed_state):
        super().__init__(fixed_state)
        self.sent_at = []  # (tick when sent, expires_tick) per command

    async def request(self, message, request_id, timeout=10):
        self.sent_at.append((self.state.tick, message.inner().body.expires_tick))
        answer = await super().request(message, request_id, timeout)
        if len(self.sent_at) == 1:
            self.state = dataclasses.replace(
                self.state, tick=self.state.tick + 1, world_version=self.state.world_version + 1
            )
        return answer


class AdvertiseThriceUntilNextTick:
    """Three commands per decision, each expiring one tick after it is made."""

    def __init__(self):
        self.decided_ticks = []

    def decide(self, decided_state):
        self.decided_ticks.append(decided_state.tick)
        return [
            actions.Advertise(
                selling=(model.Resource.RESOURCE_WATER,), expires_tick=decided_state.tick + 1
            )
            for _ in range(3)
        ]


def test_a_batch_is_dropped_once_its_tick_has_passed_and_the_agent_decides_again(tmp_path):
    """Regression: the rest of a batch went out ticks late, with expiries already
    due and accepts for offers that had closed in the meantime."""
    client = TickingClient(state(tick=5, world_version=10))
    policy = AdvertiseThriceUntilNextTick()
    asyncio.run(
        run_utility(client, policy, RunLog(tmp_path), RunOutcome(), 0.1, session="s1")
    )

    assert policy.decided_ticks == [5, 6]
    # One command from the tick-5 decision, then all three from tick 6.
    assert [tick for tick, _ in client.sent_at] == [5, 6, 6, 6]
    assert all(expires > tick for tick, expires in client.sent_at)

    logged = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    dropped = [r for r in logged if "dropped" in r]
    assert [(r["stale_after_tick"], r["now_tick"], len(r["dropped"])) for r in dropped] == [(5, 6, 2)]


class TimeoutRecordingClient(RecordingClient):
    """Records the timeout it is given on each call, rather than acting on it.

    ``request`` and ``wait_for_version`` both answer immediately regardless,
    so the recorded values show only what the runner asked for -- not whether
    a real wait would have fired.
    """

    def __init__(self, fixed_state):
        super().__init__(fixed_state)
        self.request_timeouts = []
        self.catchup_timeouts = []

    async def request(self, message, request_id, timeout=10):
        self.request_timeouts.append(timeout)
        return await super().request(message, request_id, timeout)

    async def wait_for_version(self, world_version, timeout=10):
        self.catchup_timeouts.append(timeout)
        return self.state


class AdvertiseOncePolicy:
    def decide(self, decided_state):
        return [
            actions.Advertise(
                selling=(model.Resource.RESOURCE_WATER,), expires_tick=decided_state.tick + 3
            )
        ]


@pytest.mark.parametrize(
    "tick_duration_ms, expected_command_timeout, expected_catchup_timeout",
    [
        (1000, 2.0, 1.0),  # the sandbox default
        (150, 0.3, 0.15),  # a fast game: seconds shrink, ticks of grace do not
        (5000, 10.0, 5.0),  # the live game's slowest cadence
    ],
)
def test_command_and_catchup_timeouts_scale_with_the_runs_tick_length(
    tmp_path, tick_duration_ms, expected_command_timeout, expected_catchup_timeout
):
    """Regression: both timeouts were fixed at 10s and 5s regardless of tick
    length, so a fast game could burn many ticks waiting out one slow reply."""
    built = state(rules=rules(tick_duration_ms=tick_duration_ms))
    client = TimeoutRecordingClient(built)
    asyncio.run(
        run_utility(
            client, AdvertiseOncePolicy(), RunLog(tmp_path), RunOutcome(), 0.1, session="s1"
        )
    )
    assert client.request_timeouts == [pytest.approx(expected_command_timeout)]
    assert client.catchup_timeouts == [pytest.approx(expected_catchup_timeout)]


class SettlingClient(RecordingClient):
    """Each command settles at once, as an accept or a taken offer does: the
    world's ``world_version`` moves on immediately, before the tick does.

    That is exactly what lets several decisions happen inside one tick, which
    is the scenario ``commands_sent_this_tick`` exists to guard.
    """

    async def wait_for_version(self, world_version, timeout=10):
        self.state = dataclasses.replace(self.state, world_version=self.state.world_version + 1)
        return self.state


class DecidesGivenCounts:
    """Returns a fixed number of actions on each successive call, in order.

    A real policy cannot know how many times it has already been asked this
    tick -- purity forbids it -- but nothing stops a later decision in the
    same tick from legitimately finding more candidates than an earlier one
    did (a new peer's listing arrives, say). This stands in for that.
    """

    def __init__(self, counts):
        self.counts = list(counts)
        self.decided_ticks = []

    def decide(self, decided_state):
        self.decided_ticks.append(decided_state.tick)
        n = self.counts[min(len(self.decided_ticks) - 1, len(self.counts) - 1)]
        return [
            actions.Advertise(
                selling=(model.Resource.RESOURCE_WATER,), expires_tick=decided_state.tick + 3
            )
            for _ in range(n)
        ]


def test_a_second_decisions_actions_are_dropped_once_the_ticks_budget_is_spent(tmp_path):
    """Regression: each decision's own limit check has no memory of what an
    earlier decision already sent this same tick.  Our own commands settle
    immediately -- before the tick moves on -- so two decisions of 6 and 10
    against a 10-per-tick budget used to send 16, not 10."""
    built = state(tick=5, rules=rules(new_commands_per_station_per_tick=10))
    client = SettlingClient(built)
    policy = DecidesGivenCounts([6, 10])
    asyncio.run(run_utility(client, policy, RunLog(tmp_path), RunOutcome(), 0.1, session="s1"))

    assert policy.decided_ticks == [5, 5]  # both decisions happened in the same tick
    assert len(client.request_ids) == 10  # 6 from the first decision, 4 of the second's 10

    logged = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    dropped = [r for r in logged if "budget_exhausted_at_tick" in r]
    assert [(r["budget_exhausted_at_tick"], len(r["dropped"])) for r in dropped] == [(5, 6)]


def test_no_third_decision_once_the_ticks_whole_budget_is_already_spent(tmp_path):
    """A decision that would have nothing left to send is never even made --
    the loop waits for the tick to advance instead of spinning on it."""
    built = state(tick=5, rules=rules(new_commands_per_station_per_tick=10))
    client = SettlingClient(built)
    policy = DecidesGivenCounts([10, 5])
    asyncio.run(run_utility(client, policy, RunLog(tmp_path), RunOutcome(), 0.1, session="s1"))

    assert policy.decided_ticks == [5]  # the second decision never starts
    assert len(client.request_ids) == 10


class RateLimitingClient(SettlingClient):
    """Answers every command RESULT_CODE_RATE_LIMITED, as the server would if
    another session already spent this tick's budget before this one's own
    count -- freshly zeroed by a restart -- had any way of knowing that."""

    async def request(self, message, request_id, timeout=10):
        self.request_ids.append(request_id)
        return result(request_id, ok=False, code=model.ResultCode.RESULT_CODE_RATE_LIMITED)


def test_a_rate_limited_result_is_taken_as_the_truth_over_our_own_count(tmp_path):
    """If the server still says no despite our own count saying there was
    room -- a session restarted mid-tick, say, with no memory of what it had
    already sent before it died -- its answer wins: the rest of this tick's
    actions are dropped rather than tried anyway."""
    built = state(tick=5, rules=rules(new_commands_per_station_per_tick=10))
    client = RateLimitingClient(built)
    policy = DecidesGivenCounts([10])
    asyncio.run(run_utility(client, policy, RunLog(tmp_path), RunOutcome(), 0.1, session="s1"))

    assert len(client.request_ids) == 1  # stopped as soon as the first RATE_LIMITED came back

    logged = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    dropped = [r for r in logged if "budget_exhausted_at_tick" in r]
    assert [(r["budget_exhausted_at_tick"], len(r["dropped"])) for r in dropped] == [(5, 9)]
