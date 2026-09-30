"""The live dashboard: what it shows, how it tracks actions, and how it serves.

Most of this is the pure ``view.render`` and the ``Dashboard`` hub, driven with
synthetic states from ``factories``.  The server tests bind port 0 on localhost
and use plain ``asyncio.run``, as ``test_runner.py`` does.
"""

import asyncio
import json
import urllib.request
from collections import Counter
from pathlib import Path

from websockets.asyncio.client import connect

from bazaar import actions, config
from bazaar.dashboard import Dashboard, DashboardServer
from bazaar.dashboard.view import highlights, notable_changes, our_side, render
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, fan_out, run_utility
from bazaar.validation import decode, model

from .factories import (COMPONENTS, FOOD, WATER, advertisement, bundle, observation, offer, result, rules,
                        state)

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


def test_net_per_tick_is_production_minus_upkeep():
    me = observation(last_production=bundle(12, 0, 0), upkeep_per_tick=bundle(6, 5, 0))
    view = render(state(observation=me))
    assert [r["net_per_tick"] for r in view["reserves"]] == [6, -5, 0]


def test_a_resource_we_make_faster_than_we_burn_is_never_short():
    # The hustler sells its specialty as fast as it is made, so the hold sits near zero.
    me = observation(inventory=bundle(0, 30, 30), last_production=bundle(6, 0, 0), upkeep_per_tick=bundle(1, 1, 1))
    view = render(state(observation=me))
    water = reserve(view, "water")
    assert (water["self_sufficient"], water["level"]) == (True, "ok")
    assert view["verdict"]["tone"] == "good"
    assert "food" in view["verdict"]["body"]


def test_an_empty_reserve_says_so_rather_than_zero_ticks():
    me = observation(inventory=bundle(30, 0, 30), upkeep_per_tick=bundle(1, 1, 1))
    assert render(state(observation=me))["verdict"]["body"].startswith("None left, under the 2-tick line.")


# --- the verdict line ---------------------------------------------------------------

def test_the_verdict_is_calm_when_every_reserve_is_comfortable():
    verdict = render(state())["verdict"]
    assert verdict["tone"] == "good"
    assert "water" in verdict["body"]  # ties go to the first in resource order


def test_the_verdict_names_the_lowest_reserve_and_mentions_the_others():
    me = observation(inventory=bundle(30, 4, 3), upkeep_per_tick=bundle(1, 1, 1))
    verdict = render(state(observation=me))["verdict"]
    assert verdict["tone"] == "warning"
    assert verdict["title"] == "Components is running low"
    assert "food (4.0)" in verdict["body"]


def test_a_critical_reserve_makes_a_critical_verdict():
    me = observation(inventory=bundle(30, 1, 30), upkeep_per_tick=bundle(1, 1, 1))
    verdict = render(state(observation=me))["verdict"]
    assert (verdict["tone"], verdict["title"]) == ("critical", "Food is nearly out")


def test_unmet_upkeep_outranks_a_merely_low_reserve_and_points_at_help():
    me = observation(inventory=bundle(30, 0, 3), upkeep_per_tick=bundle(1, 1, 1), last_unmet_upkeep=bundle(0, 1, 0))
    help_ = offer(proposer_id="P02", recipient_id="P01", give=(0, 3, 0), receive=(1, 0, 0), expires_tick=5)
    verdict = render(state(observation=me, offers=[help_]))["verdict"]
    assert verdict["title"] == "Losing health: food has run out"
    assert "P02 has an offer that would send food" in verdict["body"]


def test_outside_a_running_phase_the_verdict_just_names_the_phase():
    verdict = render(state(phase=model.Phase.PHASE_FINISHED))["verdict"]
    assert verdict == {"tone": "neutral", "title": "Run finished", "body": ""}


def test_once_there_is_an_outcome_the_verdict_reports_it():
    me = observation(health=0, failed_once=True, first_failure_tick=104)
    outcome = model.PlayerOutcome(collective_success=False, self_failed=True, aborted=False)
    verdict = render(state(phase=model.Phase.PHASE_FINISHED, observation=me, outcome=outcome))["verdict"]
    assert verdict["title"] == "Run over: our station failed"
    assert verdict["body"] == "Health reached 0 at tick 104. The stations failed collectively."


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


def test_offer_history_counts_how_our_offers_ended():
    offers = [
        offer("offer-1", status=model.OfferStatus.OFFER_STATUS_ACCEPTED),
        offer("offer-2", status=model.OfferStatus.OFFER_STATUS_ACCEPTED),
        offer("offer-3", status=model.OfferStatus.OFFER_STATUS_EXPIRED),
        offer("offer-4"),
    ]
    history = render(state(offers=offers))["offers"]["history"]
    assert history == {"total": 4, "accepted": 2, "expired": 1, "open": 1}


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


# --- the map ------------------------------------------------------------------------------

def station(view, station_id):
    return next(s for s in view["stations"] if s["station"] == station_id)


def test_the_map_shows_every_other_station_with_what_it_lists():
    listing = advertisement(station_id="P02", selling=(FOOD,), seeking=(WATER,))
    view = render(state(advertisements=[listing]))
    assert [s["station"] for s in view["stations"]] == ["P02"]
    assert (station(view, "P02")["sells"], station(view, "P02")["seeks"]) == (["food"], ["water"])


def test_a_station_we_dealt_with_but_the_directory_omits_still_gets_a_planet():
    ours = offer("offer-1", proposer_id="P01", recipient_id="P07")
    view = render(state(offers=[ours]))
    assert [s["station"] for s in view["stations"]] == ["P02", "P07"]
    assert station(view, "P07")["outgoing"][0]["offer_id"] == "offer-1"
    assert station(view, "P02")["sells"] == []


def test_open_offers_sit_with_the_station_on_the_other_side():
    theirs = offer("offer-in", proposer_id="P02", recipient_id="P01", give=(0, 3, 0), receive=(5, 0, 0))
    ours = offer("offer-out", proposer_id="P01", recipient_id="P02", give=(4, 0, 0), receive=(0, 0, 2))
    p02 = station(render(state(offers=[theirs, ours])), "P02")
    assert [o["offer_id"] for o in p02["incoming"]] == ["offer-in"]
    assert p02["incoming"][0]["we_get"]["food"] == 3
    assert [o["offer_id"] for o in p02["outgoing"]] == ["offer-out"]


def test_cargo_is_in_flight_only_just_after_a_trade_settles():
    settings = config.DashboardSettings(cargo_ticks=2)
    txs = [transaction("tx-1", settled_tick=3, settled_version=4),
           transaction("tx-2", settled_tick=5, settled_version=6)]
    fresh = station(render(state(tick=6, transactions=txs), settings=settings), "P02")
    assert (fresh["trades_total"], fresh["last_trade"]["transaction_id"], fresh["cargo"]) == (2, "tx-2", True)
    assert station(render(state(tick=7, transactions=txs), settings=settings), "P02")["cargo"] is False


# --- what the agent is after ----------------------------------------------------------------

def goal(view, resource):
    return next(g for g in view["plan"]["goals"] if g["resource"] == resource)


def test_offers_out_for_a_resource_mean_the_agent_is_buying_it_with_what_they_pay():
    buying = [offer(f"offer-{n}", proposer_id="P01", recipient_id="P02", give=(4, 0, 0), receive=(0, 2, 0))
              for n in range(2)]
    help_ = offer("offer-in", proposer_id="P02", recipient_id="P01", give=(0, 3, 0), receive=(5, 0, 0))
    view = render(state(offers=[*buying, help_]))
    assert (goal(view, "food")["verb"], goal(view, "food")["text"]) == ("buy", "2 offers out, P02 offering 3 food")
    assert (goal(view, "water")["verb"], goal(view, "water")["text"]) == ("sell", "Paying with it in 2 offers")
    assert view["plan"]["headline"] == "Buying food with water"


def test_a_listing_alone_counts_as_selling_and_everything_else_is_held():
    mine = advertisement(station_id="P01", selling=(WATER,), seeking=())
    view = render(state(advertisements=[mine]))
    assert (goal(view, "water")["verb"], goal(view, "water")["text"]) == ("sell", "Listed")
    assert (goal(view, "food")["verb"], goal(view, "food")["text"]) == ("hold", "30.0 ticks left")
    assert (goal(view, "components")["verb"], goal(view, "components")["text"]) == ("hold", "Not consumed")
    assert view["plan"]["headline"] == "Selling water"


def test_buying_a_resource_that_is_nearly_out_is_urgent():
    me = observation(inventory=bundle(30, 1, 30), upkeep_per_tick=bundle(1, 1, 1))
    wanted = offer("offer-1", proposer_id="P01", recipient_id="P02", give=(3, 0, 0), receive=(0, 2, 0))
    view = render(state(observation=me, offers=[wanted]))
    assert goal(view, "food")["urgency"] == "critical"
    assert view["plan"]["headline"] == "Urgently buying food with water"


def test_outside_a_running_phase_there_is_no_plan():
    plan = render(state(phase=model.Phase.PHASE_READY))["plan"]
    assert plan == {"headline": "Waiting for the run to start", "goals": []}


# --- the last few ticks -------------------------------------------------------------------

def test_recent_counts_only_what_happened_inside_the_window():
    settings = config.DashboardSettings(recent_window_ticks=3, strip_ticks=5)
    txs = [transaction("tx-old", settled_tick=2, settled_version=3),
           transaction("tx-new", settled_tick=9, settled_version=10)]
    expired = model.OfferStatus.OFFER_STATUS_EXPIRED
    ours = offer("offer-1", proposer_id="P01", recipient_id="P02", status=expired, closed_tick=8)
    theirs = offer("offer-2", status=expired, closed_tick=8)
    failures = {1: Counter(RATE_LIMITED=5), 9: Counter(RATE_LIMITED=2, NOT_OPEN=1)}
    view = render(state(tick=10, transactions=txs, offers=[ours, theirs]), failures=failures, settings=settings)
    recent = view["recent"]
    assert (recent["from_tick"], recent["trades"], recent["expired"], recent["failures"]) == (8, 1, 1, 3)
    assert recent["top_failure"] == {"reason": "rate limited", "hint": "more than 3 commands in one tick", "count": 2}
    assert [(s["tick"], s["trades"], s["failures"]) for s in recent["strip"]] == [
        (6, 0, 0), (7, 0, 0), (8, 0, 0), (9, 1, 3), (10, 0, 0)]


def trade_event(tick, got, gave, counterparty="P02"):
    return {"tick": tick, "kind": "trade", "text": f"trade at {tick}", "counterparty": counterparty,
            "we_got": dict(zip(("water", "food", "components"), got)),
            "we_gave": dict(zip(("water", "food", "components"), gave))}


def test_the_trades_of_one_tick_fold_into_their_net_effect():
    events = [trade_event(5, (0, 1, 0), (0, 0, 2)), trade_event(6, (1, 0, 0), (0, 0, 1), "P04"),
              trade_event(6, (2, 1, 0), (1, 0, 6), "P07")]
    items = highlights(events, {}, config.DASHBOARD)
    assert [(i["tick"], i["text"]) for i in items] == [
        (6, "2 trades: +2 water, +1 food and −7 components"),
        (5, "trade at 5"),
    ]


def test_a_reserve_wobbling_across_a_line_is_reported_once_at_its_latest():
    events = [{"tick": 3, "kind": "warning", "text": "Food under 5 ticks"},
              {"tick": 4, "kind": "info", "text": "Food back above 5 ticks"},
              {"tick": 5, "kind": "warning", "text": "Food under 5 ticks"}]
    items = highlights(events, {}, config.DASHBOARD)
    assert [(i["tick"], i["text"]) for i in items] == [(5, "Food under 5 ticks"), (4, "Food back above 5 ticks")]


def test_failures_become_one_highlight_per_tick_among_the_newest_events():
    events = [{"tick": 4, "kind": "trade", "text": "+3 food from P02, for 5 water"},
              {"tick": 7, "kind": "warning", "text": "Food under 5 ticks"}]
    failures = {6: Counter(RATE_LIMITED=4), 8: Counter(RATE_LIMITED=2, NOT_OPEN=1)}
    items = highlights(events, failures, config.DashboardSettings(highlights=3))
    assert [(i["tick"], i["text"]) for i in items] == [
        (8, "3 commands failed, mostly rate limited"),
        (7, "Food under 5 ticks"),
        (6, "4 commands failed: rate limited"),
    ]


# --- what changed between states ------------------------------------------------------------

def test_the_first_state_of_a_run_reports_no_changes():
    assert notable_changes(None, state(), config.DASHBOARD) == []


def test_a_new_trade_is_reported_from_our_side():
    before = state(tick=4)
    settled = transaction("tx-1", proposer_id="P02", recipient_id="P01", give=(0, 3, 0), receive=(5, 0, 0),
                          settled_tick=4)
    [event] = notable_changes(before, state(tick=4, transactions=[settled]), config.DASHBOARD)
    assert (event["tick"], event["kind"], event["text"]) == (4, "trade", "+3 food from P02, for 5 water")
    assert (event["we_got"]["food"], event["we_gave"]["water"]) == (3, 5)


def test_a_reserve_crossing_a_line_is_reported_as_it_crosses():
    settings = config.DashboardSettings(critical_cover_ticks=2.0, low_cover_ticks=5.0)

    def at(food, unmet=0):
        me = observation(inventory=bundle(30, food, 30), upkeep_per_tick=bundle(1, 1, 1),
                         last_unmet_upkeep=bundle(0, unmet, 0))
        return state(observation=me)

    def said(before, after):
        return [e["text"] for e in notable_changes(before, after, settings)]

    assert said(at(8), at(4)) == ["Food under 5 ticks"]
    assert said(at(4), at(3)) == []
    assert said(at(4), at(1)) == ["Food under 2 ticks"]
    assert said(at(1), at(0, unmet=1)) == ["Food ran out: health is falling"]
    assert said(at(1), at(9)) == ["Food back above 5 ticks"]


def test_only_a_change_of_what_we_list_is_reported_not_a_refresh():
    unlisted = state()
    listed = state(advertisements=[advertisement(station_id="P01", selling=(WATER,), seeking=(FOOD,))])
    changed = state(advertisements=[advertisement(station_id="P01", selling=(WATER,), seeking=(COMPONENTS,))])
    assert notable_changes(listed, unlisted, config.DASHBOARD) == []   # lapsed, about to be refreshed
    assert notable_changes(unlisted, listed, config.DASHBOARD) == []
    assert [e["text"] for e in notable_changes(listed, changed, config.DASHBOARD)] == [
        "Now selling water, seeking components"]


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


def test_failed_commands_are_counted_by_the_tick_they_were_decided_in():
    dashboard, first, second = Dashboard(), advert(), advert()
    dashboard.decided(state(tick=5), [first, second])
    dashboard.sent(first, "req-1")
    code = model.ResultCode.RESULT_CODE_RATE_LIMITED
    dashboard.resolved("req-1", result("req-1", ok=False, code=code))
    dashboard.rejected(second, "expires_tick 3 must be greater than the current tick 5")
    assert dashboard.failures == {5: {"RATE_LIMITED": 1, "REJECTED_LOCALLY": 1}}


def test_the_hub_keeps_what_changed_and_forgets_it_when_a_new_run_starts():
    dashboard = Dashboard(agent="hustler")
    dashboard.on_state(state(tick=3))
    dashboard.on_state(state(tick=3, transactions=[transaction(settled_tick=3)]))
    assert [e["kind"] for e in dashboard.events] == ["trade"]
    view = dashboard.view()
    assert view["highlights"][0]["kind"] == "trade"
    assert view["header"]["agent"] == "hustler"
    dashboard.failures[3] = Counter(RATE_LIMITED=1)
    dashboard.on_state(state(tick=0, run_id="run-2"))
    assert list(dashboard.events) == [] and dashboard.failures == {}


def test_withdrawing_an_offer_names_the_planet_it_was_made_to():
    ours = offer("offer-4", proposer_id="P01", recipient_id="P05")
    dashboard = Dashboard()
    dashboard.decided(state(offers=[ours]), [actions.Withdraw(object_id="offer-4"),
                                             actions.Withdraw(object_id="advertisement-2")])
    assert [r.terms.get("counterparty") for r in dashboard.actions] == ["P05", None]


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
