"""Reporting the agent's decisions to the external dashboard, through its tap.

The tap is faked here: a reporter only ever calls ``report(event, **fields)``,
and the dashboard's own tests cover what it does with what arrives.
"""

import asyncio

from bazaar import actions
from bazaar.report import DashboardReporter, command_spec, reporter
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, fan_out, run_utility

from .factories import FOOD, WATER, result, state


class FakeTap:
    def __init__(self, active=True):
        self._active = active
        self.reports = []

    def active(self):
        return self._active

    def report(self, event, **fields):
        self.reports.append((event, fields))


def advert(expires_tick=3):
    return actions.Advertise(reason="stay visible", score=1.0, selling=(WATER,), seeking=(FOOD,),
                             expires_tick=expires_tick)


def test_without_an_active_tap_there_is_no_reporter():
    assert reporter("jesus", tap=FakeTap(active=False)) is None


def test_an_active_tap_is_told_the_agents_name_first():
    tap = FakeTap()
    assert reporter("jesus", tap=tap) is not None
    assert tap.reports == [("agent", {"name": "jesus"})]


def test_a_decision_is_reported_with_keys_that_later_reports_refer_to():
    tap = FakeTap()
    activity = DashboardReporter(tap.report)
    first, twin = advert(), advert()
    activity.decided(state(tick=4, world_version=9), [first, twin])
    activity.sent(twin, "req-2")
    activity.rejected(first, "too late")
    activity.errored("req-2", "TimeoutError")
    (event, decided), *rest = tap.reports
    assert (event, decided["tick"], decided["world_version"]) == ("decided", 4, 9)
    assert [a["key"] for a in decided["actions"]] == [1, 2]
    assert decided["actions"][0]["reason"] == "stay visible"
    assert rest == [("sent", {"key": 2, "request_id": "req-2"}), ("rejected", {"key": 1, "detail": "too late"}),
                    ("errored", {"request_id": "req-2", "detail": "TimeoutError"})]


def test_results_are_left_to_the_wire():
    tap = FakeTap()
    DashboardReporter(tap.report).resolved("req-1", result("req-1"))
    assert tap.reports == []


def test_each_kind_of_action_is_reported_in_the_dashboards_terms():
    assert command_spec(advert()) == {"kind": "advertise", "selling": ["RESOURCE_WATER"],
                                      "seeking": ["RESOURCE_FOOD"], "expires_tick": 3}
    assert command_spec(actions.Offer(recipient_id="P02", give=(2, 0, 0), receive=(0, 1, 0), expires_tick=9)) == {
        "kind": "offer", "recipient_id": "P02", "give": [2, 0, 0], "receive": [0, 1, 0], "expires_tick": 9}
    assert command_spec(actions.Accept(offer_id="offer-1")) == {"kind": "accept", "offer_id": "offer-1"}
    assert command_spec(actions.Withdraw(object_id="ad-1")) == {"kind": "withdraw", "object_id": "ad-1"}


def test_a_broken_tap_never_breaks_the_runner():
    def broken(event, **fields):
        raise RuntimeError("socket gone")

    activity = DashboardReporter(broken)
    action = advert()
    activity.decided(state(), [action])
    activity.sent(action, "req-1")
    activity.rejected(action, "why")
    activity.errored("req-1", "why")


def test_fan_out_calls_every_observer_and_ignores_missing_ones():
    calls = []
    assert fan_out(None, None) is None
    single = calls.append
    assert fan_out(None, single) is single
    fan_out(lambda s: calls.append(("a", s)), None, lambda s: calls.append(("b", s)))("x")
    assert calls == [("a", "x"), ("b", "x")]


# --- wired into the runner --------------------------------------------------------------

class AnsweringClient:
    """Enough of BazaarClient for run_utility to send one command and get OK."""

    def __init__(self, fixed_state):
        self.state = fixed_state
        self.sent = 0

    async def first_state(self):
        return self.state

    async def declare_ready(self, snapshot_sequence, ready=True, timeout=10):
        return object()

    async def request(self, message, request_id, timeout=10):
        self.sent += 1
        return result(request_id, processed_version=self.state.world_version)

    async def wait_for_version(self, world_version, timeout=10):
        return self.state

    async def quiet(self, seconds=0.4):
        await asyncio.sleep(min(seconds, 0.02))
        return None


class AdvertiseOncePolicy:
    def decide(self, decided_state):
        return [advert(expires_tick=decided_state.tick + 3)]


def test_the_runner_reports_each_action_it_decides_and_sends(tmp_path):
    tap = FakeTap()
    client = AnsweringClient(state(tick=2))
    asyncio.run(run_utility(client, AdvertiseOncePolicy(), RunLog(tmp_path), RunOutcome(), 0.2,
                            activity=DashboardReporter(tap.report), session="s1"))
    assert client.sent == 1
    assert [event for event, _ in tap.reports] == ["decided", "sent"]
    assert tap.reports[1][1] == {"key": 1, "request_id": "utility-s1-0001"}
