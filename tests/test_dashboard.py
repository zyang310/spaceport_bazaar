"""The live dashboard: what it shows, how it tracks actions, and how it serves.

Most of this is the pure ``view.render`` and the ``Dashboard`` hub, driven with
synthetic states from ``factories``.  The server tests bind port 0 on localhost
and use plain ``asyncio.run``, as ``test_runner.py`` does.
"""

import asyncio
import json
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

from bazaar import actions, config
from bazaar.dashboard import Dashboard, DashboardServer
from bazaar.dashboard.view import our_side, render
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, fan_out, run_utility
from bazaar.validation import decode, model

from .factories import COMPONENTS, FOOD, WATER, bundle, observation, offer, result, rules, state

FIXTURES = Path(__file__).parent / "fixtures"


def transaction(transaction_id="tx-1", proposer_id="P02", recipient_id="P01",
                give=(0, 0, 1), receive=(1, 0, 0), settled_version=5, settled_tick=0):
    return model.Transaction(
        transaction_id=transaction_id,
        offer_id="offer-" + transaction_id,
        proposer_id=proposer_id,
        recipient_id=recipient_id,
        give=model.Bundle.of(give),
        receive=model.Bundle.of(receive),
        settled_tick=settled_tick,
        settled_version=settled_version,
    )


def reserve(view, resource):
    return next(r for r in view["reserves"] if r["resource"] == resource)


# --- whose side of the deal -----------------------------------------------------

def test_an_offer_we_proposed_reads_as_we_give_what_it_gives():
    ours = offer(proposer_id="P01", recipient_id="P02", give=(2, 0, 0), receive=(0, 1, 0))
    assert our_side(ours, "P01") == ("P02", bundle(2, 0, 0), bundle(0, 1, 0))


def test_an_offer_addressed_to_us_is_swapped_to_our_side():
    theirs = offer(proposer_id="P02", recipient_id="P01", give=(0, 0, 3), receive=(1, 0, 0))
    assert our_side(theirs, "P01") == ("P02", bundle(1, 0, 0), bundle(0, 0, 3))


def test_a_transaction_where_we_were_the_recipient_is_swapped_too():
    settled = transaction(proposer_id="P03", recipient_id="P01", give=(0, 5, 0), receive=(2, 0, 0))
    assert our_side(settled, "P01") == ("P03", bundle(2, 0, 0), bundle(0, 5, 0))


# --- reserves -----------------------------------------------------------------------

def test_reserves_come_out_in_the_rules_resource_order():
    view = render(state(rules=rules(resource_order=[COMPONENTS, WATER, FOOD])))
    assert [r["resource"] for r in view["reserves"]] == ["components", "water", "food"]


def test_a_resource_with_no_upkeep_has_no_cover_rather_than_a_division_error():
    view = render(state(observation=observation(upkeep_per_tick=bundle(1, 1, 0))))
    components = reserve(view, "components")
    assert components["cover_ticks"] is None
    assert components["level"] == "ok"


def test_cover_levels_follow_the_configured_thresholds():
    settings = config.DashboardSettings(critical_cover_ticks=2.0, low_cover_ticks=5.0)
    me = observation(inventory=bundle(1, 4, 10), upkeep_per_tick=bundle(1, 1, 2))
    view = render(state(observation=me), settings=settings)
    assert reserve(view, "water")["level"] == "critical"   # 1 tick
    assert reserve(view, "food")["level"] == "low"         # 4 ticks
    assert reserve(view, "components")["level"] == "ok"    # 5 ticks, not below the line
    assert reserve(view, "components")["cover_ticks"] == 5.0


def test_only_the_specialty_is_flagged_as_the_specialty():
    view = render(state(observation=observation(specialty=FOOD)))
    assert [r["resource"] for r in view["reserves"] if r["is_specialty"]] == ["food"]


# --- offers and trades ------------------------------------------------------------------

def test_expired_incoming_offers_are_left_out_of_the_open_offers():
    live = offer("offer-live", expires_tick=9)
    stale = offer("offer-stale", expires_tick=3)
    view = render(state(tick=5, offers=[live, stale]))
    assert [o["offer_id"] for o in view["offers"]["incoming"]] == ["offer-live"]
    assert view["offers"]["incoming"][0]["ticks_left"] == 4


def test_outgoing_offers_count_against_the_slot_limit():
    mine = [offer(f"offer-{n}", proposer_id="P01", recipient_id="P02") for n in range(2)]
    view = render(state(offers=mine, rules=rules(max_open_outgoing_offers=3)))
    assert len(view["offers"]["outgoing"]) == 2
    assert view["offers"]["slots_left"] == 1
    assert view["offers"]["market_open"] == 2


def test_recent_trades_are_only_ours_newest_first_and_capped():
    txs = [
        transaction("tx-old", settled_version=3),
        transaction("tx-strangers", proposer_id="P05", recipient_id="P06", settled_version=9),
        transaction("tx-new", proposer_id="P01", recipient_id="P02", settled_version=7),
        transaction("tx-mid", settled_version=5),
    ]
    view = render(state(transactions=txs), settings=config.DashboardSettings(recent_trades=2))
    assert [t["transaction_id"] for t in view["trades"]["recent"]] == ["tx-new", "tx-mid"]
    assert view["trades"]["total"] == 3
    assert view["trades"]["recent"][0]["we_proposed"] is True


# --- the action lifecycle --------------------------------------------------------------

def advert(expires_tick=3):
    return actions.Advertise(reason="stay visible", score=1.0, selling=(WATER,), seeking=(FOOD,),
                             expires_tick=expires_tick)


def test_an_action_moves_from_queued_to_sent_to_ok():
    dashboard, decided = Dashboard(), state()
    action = advert()
    dashboard.decided(decided, [action])
    assert dashboard.actions[-1].status == "queued"
    dashboard.sent(action, "req-1")
    assert dashboard.actions[-1].status == "sent"
    dashboard.resolved("req-1", result("req-1"))
    record = dashboard.actions[-1]
    assert (record.status, record.detail) == ("ok", "OK")
    assert dashboard.totals == {"sent": 1, "ok": 1}


def test_a_failed_result_keeps_its_code():
    dashboard, action = Dashboard(), advert()
    dashboard.decided(state(), [action])
    dashboard.sent(action, "req-1")
    code = model.ResultCode.RESULT_CODE_REQUEST_ID_CONFLICT
    dashboard.resolved("req-1", result("req-1", ok=False, code=code))
    assert (dashboard.actions[-1].status, dashboard.actions[-1].detail) == ("failed", "REQUEST_ID_CONFLICT")


def test_a_locally_rejected_action_says_why():
    dashboard, action = Dashboard(), advert()
    dashboard.decided(state(), [action])
    dashboard.rejected(action, "expires_tick 3 must be greater than the current tick 3")
    assert dashboard.actions[-1].status == "rejected"
    assert "expires_tick" in dashboard.actions[-1].detail


def test_actions_never_sent_become_skipped_at_the_next_decision():
    dashboard = Dashboard()
    first, second = advert(3), advert(4)
    dashboard.decided(state(world_version=2), [first, second])
    dashboard.sent(first, "req-1")
    dashboard.decided(state(world_version=3), [])
    assert [r.status for r in dashboard.actions] == ["sent", "skipped"]
    assert dashboard.last_decision == {"world_version": 3, "tick": 0, "count": 0}


def test_two_identical_actions_are_tracked_as_two_records():
    dashboard = Dashboard()
    first, twin = advert(), advert()
    assert first == twin and first is not twin
    dashboard.decided(state(), [first, twin])
    dashboard.sent(twin, "req-2")
    assert [r.request_id for r in dashboard.actions] == [None, "req-2"]


def test_an_accept_shows_the_terms_of_the_offer_it_accepts():
    incoming = offer("offer-9", proposer_id="P02", give=(0, 3, 0), receive=(1, 0, 0))
    dashboard = Dashboard()
    dashboard.decided(state(offers=[incoming]), [actions.Accept(offer_id="offer-9")])
    terms = dashboard.actions[-1].terms
    assert terms["counterparty"] == "P02"
    assert (terms["we_give"], terms["we_get"]) == ({"water": 1, "food": 0, "components": 0},
                                                   {"water": 0, "food": 3, "components": 0})


# --- history and timing -----------------------------------------------------------------

def test_history_keeps_one_point_per_tick_and_is_bounded():
    dashboard = Dashboard(settings=config.DashboardSettings(history_ticks=3))
    for tick, water in [(0, 30), (0, 29), (1, 28), (2, 27), (3, 26)]:
        dashboard.on_state(state(tick=tick, observation=observation(inventory=bundle(water, 30, 30))))
    assert [(t, inv.water) for t, inv in dashboard.history] == [(1, 28), (2, 27), (3, 26)]
    assert reserve(dashboard.view(), "water")["history"] == [[1, 28], [2, 27], [3, 26]]


def test_the_tick_clock_restarts_only_when_the_tick_changes():
    now = [1000.0]
    dashboard = Dashboard(clock=lambda: now[0])
    dashboard.on_state(state(tick=4, world_version=10))
    now[0] = 1002.0
    dashboard.on_state(state(tick=4, world_version=11))
    assert dashboard.tick_seen_at_ms == 1_000_000
    now[0] = 1005.0
    dashboard.on_state(state(tick=5, world_version=12))
    assert dashboard.tick_seen_at_ms == 1_005_000


# --- robustness -------------------------------------------------------------------------

def test_the_view_is_json_for_a_synthetic_state():
    dashboard = Dashboard()
    dashboard.on_state(state(offers=[offer()], transactions=[transaction()]))
    dashboard.decided(dashboard.state, [advert()])
    json.dumps(dashboard.view())


def test_the_view_is_json_for_every_real_captured_state():
    frames = sorted(FIXTURES.glob("*_state.bin"))
    assert frames
    dashboard = Dashboard()
    for frame in frames:
        dashboard.on_state(decode.decode_bytes(frame.read_bytes()).state)
        json.loads(dashboard.payload())
    assert dashboard.errors == 0


def test_the_view_before_any_state_says_so():
    assert Dashboard().view()["state"] is None


def test_a_broken_hook_is_counted_rather_than_raised():
    dashboard = Dashboard()
    dashboard.on_state("not a state")
    assert dashboard.errors == 1
    assert "on_state" in dashboard.last_error


def test_a_failing_subscriber_does_not_stop_the_others():
    dashboard, seen = Dashboard(), []

    def broken(payload):
        raise RuntimeError("socket gone")

    dashboard.subscribe(broken)
    dashboard.subscribe(seen.append)
    dashboard.on_state(state())
    assert len(seen) == 1
    assert dashboard.errors == 1


def test_fan_out_calls_every_observer_and_ignores_missing_ones():
    calls = []
    assert fan_out(None, None) is None
    single = calls.append
    assert fan_out(None, single) is single
    fan_out(lambda s: calls.append(("a", s)), None, lambda s: calls.append(("b", s)))("x")
    assert calls == [("a", "x"), ("b", "x")]


# --- the server -------------------------------------------------------------------------

def fetch(url: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers["Content-Type"], response.read().decode()


def test_the_server_serves_the_page_the_view_and_a_live_feed():
    async def scenario():
        dashboard = Dashboard()
        server = DashboardServer(dashboard)
        url = await server.start("127.0.0.1", 0)
        try:
            status, content_type, body = await asyncio.to_thread(fetch, url)
            assert status == 200 and content_type.startswith("text/html")
            assert "<title>Bazaar dashboard</title>" in body

            dashboard.on_state(state(tick=7))
            status, content_type, body = await asyncio.to_thread(fetch, url + "view.json")
            assert content_type == "application/json"
            assert json.loads(body)["header"]["tick"] == 7

            async with connect(f"ws://127.0.0.1:{server.port}/ws") as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert first["header"]["tick"] == 7  # the whole picture at once
                dashboard.on_state(state(tick=8))
                pushed = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert pushed["header"]["tick"] == 8
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_an_unknown_path_is_a_404():
    async def scenario():
        server = DashboardServer(Dashboard())
        url = await server.start("127.0.0.1", 0)
        try:
            await asyncio.to_thread(fetch, url + "nope")
        except urllib.error.HTTPError as exc:
            return exc.code
        finally:
            await server.stop()

    assert asyncio.run(scenario()) == 404


def test_a_taken_port_falls_back_to_a_free_one():
    async def scenario():
        first, second = DashboardServer(Dashboard()), DashboardServer(Dashboard())
        await first.start("127.0.0.1", 0)
        try:
            await second.start("127.0.0.1", first.port)
            try:
                assert second.port != first.port
            finally:
                await second.stop()
        finally:
            await first.stop()

    asyncio.run(scenario())


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


def test_the_runner_reports_each_action_to_the_dashboard(tmp_path):
    dashboard = Dashboard()
    client = AnsweringClient(state(tick=2))
    asyncio.run(run_utility(client, AdvertiseOncePolicy(), RunLog(tmp_path), RunOutcome(), 0.2,
                            activity=dashboard, session="s1"))
    assert client.sent == 1
    assert [(r.status, r.request_id) for r in dashboard.actions] == [("ok", "utility-s1-0001")]
