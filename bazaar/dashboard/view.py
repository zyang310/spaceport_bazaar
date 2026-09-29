"""What the dashboard shows, computed from one state as plain JSON.

Pure, like a policy: no clock, no I/O, no memory of its own.  Everything that
needs remembering -- inventory over time, the actions we tried -- is handed in
by ``hub.Dashboard``, which is what lets a captured fixture render exactly as
it would live.

The one interpretive step is :func:`our_side`.  Offers and transactions record
amounts from the *proposer's* point of view, so "give 2 water" means we pay two
water only when we proposed it.  Every row the page shows goes through that one
function, so the direction of a deal is decided in exactly one place.
"""

from collections import Counter
from dataclasses import dataclass, field

from .. import actions as act
from .. import config
from ..brain import store
from ..validation import model


@dataclass
class ActionRecord:
    """One thing the policy decided, and how far it got.

    Mutable on purpose, unlike everything decoded from the server: this is our
    own bookkeeping, and the status moves as the command travels.
    """

    #: Kept so the runner's later hooks can find this record by identity.
    action: act.Action
    world_version: int
    tick: int
    description: str
    reason: str
    score: float
    #: The action's terms, structured for the page; see :func:`action_terms`.
    terms: dict = field(default_factory=dict)
    #: queued -> sent -> ok | failed; or rejected, error, skipped.
    status: str = "queued"
    request_id: str | None = None
    #: The result code, or why the command never got one.
    detail: str | None = None


def resource_key(resource: model.Resource) -> str:
    """``RESOURCE_WATER`` -> ``water``, matching the Bundle field names."""
    return resource.name.removeprefix("RESOURCE_").lower()


def bundle_json(bundle: model.Bundle) -> dict[str, int]:
    return {"water": bundle.water, "food": bundle.food, "components": bundle.components}


def our_side(deal, self_id: str) -> tuple[str, model.Bundle, model.Bundle]:
    """``(counterparty, we_give, we_get)`` for an offer or transaction we are in."""
    if deal.proposer_id == self_id:
        return deal.recipient_id, deal.give, deal.receive
    return deal.proposer_id, deal.receive, deal.give


def cover_ticks(amount: int, upkeep: int) -> float | None:
    """How many ticks the stock lasts at current upkeep; ``None`` if unused."""
    if upkeep <= 0:
        return None
    return amount / upkeep


def cover_level(cover: float | None, settings: config.DashboardSettings) -> str:
    if cover is None:
        return "ok"
    if cover < settings.critical_cover_ticks:
        return "critical"
    if cover < settings.low_cover_ticks:
        return "low"
    return "ok"


def action_terms(action: act.Action, state: model.State) -> dict:
    """An action's terms from our side, so the page can draw them, not parse them.

    An accept names only an offer ID; its terms are looked up in the state the
    policy decided on, where that offer was still open.
    """
    if isinstance(action, act.Offer):
        return {
            "kind": "offer",
            "counterparty": action.recipient_id,
            "we_give": bundle_json(model.Bundle.of(action.give)),
            "we_get": bundle_json(model.Bundle.of(action.receive)),
            "expires_tick": action.expires_tick,
        }
    if isinstance(action, act.Accept):
        terms = {"kind": "accept", "offer_id": action.offer_id}
        for offer in state.offers:
            if offer.offer_id == action.offer_id:
                counterparty, give, get = our_side(offer, state.self_station_id)
                terms.update(counterparty=counterparty, we_give=bundle_json(give), we_get=bundle_json(get))
                break
        return terms
    if isinstance(action, act.Advertise):
        return {
            "kind": "advertise",
            "selling": [resource_key(r) for r in action.selling],
            "seeking": [resource_key(r) for r in action.seeking],
            "expires_tick": action.expires_tick,
        }
    if isinstance(action, act.Withdraw):
        terms = {"kind": "withdraw", "object_id": action.object_id}
        # Withdrawing an offer concerns the planet it was made to; a listing, nobody.
        for offer in state.offers:
            if offer.offer_id == action.object_id:
                terms["counterparty"] = our_side(offer, state.self_station_id)[0]
                break
        return terms
    return {"kind": type(action).__name__.lower()}


def render(
    state: model.State,
    *,
    history=(),
    actions=(),
    totals: dict | None = None,
    last_decision: dict | None = None,
    tick_seen_at_ms: float | None = None,
    settings: config.DashboardSettings = config.DASHBOARD,
) -> dict:
    """The whole page's data for one state.

    ``history`` is ``(tick, Bundle)`` pairs, oldest first.  ``actions`` is
    :class:`ActionRecord` objects, oldest first.
    """
    reserves = [_reserve(state, r, history, settings) for r in state.rules.resource_order]
    offers = _offers(state)
    return {
        "header": _header(state, tick_seen_at_ms),
        "verdict": verdict(state, reserves, offers["incoming"], settings),
        "health": _health(state),
        "reserves": reserves,
        "cover_scale": {
            "critical_ticks": settings.critical_cover_ticks,
            "low_ticks": settings.low_cover_ticks,
            "max_ticks": settings.cover_gauge_ticks,
        },
        "offers": offers,
        "trades": _trades(state, settings.recent_trades),
        "actions": _actions(state, actions, totals, last_decision, settings.recent_actions),
    }


PHASE_HEADLINES = {
    model.Phase.PHASE_READY: "Waiting for the run to start",
    model.Phase.PHASE_PAUSED: "Run paused",
    model.Phase.PHASE_FINISHED: "Run finished",
    model.Phase.PHASE_ABORTED: "Run aborted",
}


def _ticks(cover: float) -> str:
    return f"{cover:.1f} tick{'' if cover == 1 else 's'}"


def verdict(state: model.State, reserves: list[dict], incoming: list[dict],
            settings: config.DashboardSettings) -> dict:
    """The one sentence the page leads with: the most urgent thing, in words.

    ``tone`` is ``good``, ``warning``, ``critical`` or ``neutral``.  Only the
    worst reserve gets the headline; the others ride along in the body, so the
    line stays readable when everything goes wrong at once.
    """
    me = state.observation
    outcome = state.outcome
    if outcome is not None:
        together = {True: "The stations succeeded together.", False: "The stations failed collectively.", None: ""}
        if outcome.aborted:
            return {"tone": "critical", "title": "Run aborted", "body": together[outcome.collective_success]}
        if outcome.self_failed:
            return {"tone": "critical", "title": "Run over: our station failed",
                    "body": f"Health reached 0 at tick {me.first_failure_tick}. {together[outcome.collective_success]}".strip()}
        return {"tone": "good", "title": "Run over: our station survived",
                "body": together[outcome.collective_success]}
    if state.phase is not model.Phase.PHASE_RUNNING:
        return {"tone": "neutral", "title": PHASE_HEADLINES.get(state.phase, "Not running"), "body": ""}
    if me.health <= 0:
        return {"tone": "critical", "title": "Our station has failed",
                "body": f"Health reached 0 at tick {me.first_failure_tick}."}

    def helpers(resource: str) -> str:
        senders = [o["counterparty"] for o in incoming if o["we_get"][resource] > 0]
        if not senders:
            return ""
        return f" {', '.join(senders)} {'has an offer' if len(senders) == 1 else 'have offers'} that would send {resource}."

    unmet = [r for r in reserves if r["last_unmet"]]
    if unmet:
        worst = max(unmet, key=lambda r: r["last_unmet"])
        others = [r["resource"] for r in unmet if r is not worst]
        body = f"{worst['last_unmet']} {worst['resource']} short last tick, and health falls every tick until it is restocked."
        if others:
            body += f" Also short: {', '.join(others)}."
        return {"tone": "critical", "title": f"Losing health: {worst['resource']} has run out",
                "body": body + helpers(worst["resource"])}

    consumed = sorted((r for r in reserves if r["cover_ticks"] is not None), key=lambda r: r["cover_ticks"])
    if not consumed:
        return {"tone": "good", "title": "All reserves are comfortable", "body": "Nothing is being consumed."}
    worst = consumed[0]
    if worst["level"] == "ok":
        return {"tone": "good", "title": "All reserves are comfortable",
                "body": f"The tightest is {worst['resource']}, with {_ticks(worst['cover_ticks'])} left."}

    line = settings.critical_cover_ticks if worst["level"] == "critical" else settings.low_cover_ticks
    body = f"{_ticks(worst['cover_ticks'])} left, under the {line:g}-tick line."
    others = [f"{r['resource']} ({r['cover_ticks']:.1f})" for r in consumed[1:] if r["level"] != "ok"]
    if others:
        body += f" Also low: {', '.join(others)}."
    title = f"{worst['resource'].capitalize()} is {'nearly out' if worst['level'] == 'critical' else 'running low'}"
    return {"tone": "critical" if worst["level"] == "critical" else "warning", "title": title,
            "body": body + helpers(worst["resource"])}


def _header(state: model.State, tick_seen_at_ms: float | None) -> dict:
    outcome = state.outcome
    return {
        "station": state.self_station_id,
        "names": {entry.station_id: entry.display_name for entry in state.directory},
        "specialty": resource_key(state.observation.specialty),
        "run_id": state.run_id,
        "phase": state.phase.name.removeprefix("PHASE_"),
        "tick": state.tick,
        "duration_ticks": state.rules.duration_ticks,
        "tick_duration_ms": state.rules.tick_duration_ms,
        "tick_seen_at_ms": tick_seen_at_ms,
        "world_version": state.world_version,
        "snapshot_sequence": state.snapshot_sequence,
        "outcome": None
        if outcome is None
        else {
            "collective_success": outcome.collective_success,
            "self_failed": outcome.self_failed,
            "aborted": outcome.aborted,
        },
    }


def _health(state: model.State) -> dict:
    me = state.observation
    return {
        "health": me.health,
        "max_health": state.rules.max_health,
        "current_shortage_streak": me.current_shortage_streak,
        "longest_shortage_streak": me.longest_shortage_streak,
        "shortage_ticks": me.shortage_ticks,
        "fully_supplied_ticks": me.fully_supplied_ticks,
        "failed_once": me.failed_once,
        "first_failure_tick": me.first_failure_tick,
    }


def _reserve(state: model.State, resource: model.Resource, history, settings) -> dict:
    me = state.observation
    amount = me.inventory.get(resource)
    upkeep = me.upkeep_per_tick.get(resource)
    cover = cover_ticks(amount, upkeep)
    return {
        "resource": resource_key(resource),
        "amount": amount,
        "upkeep": upkeep,
        "cover_ticks": None if cover is None else round(cover, 1),
        "level": cover_level(cover, settings),
        "is_specialty": resource is me.specialty,
        "last_production": me.last_production.get(resource),
        # Before trades: what the station's own economy does to this stock.
        "net_per_tick": me.last_production.get(resource) - upkeep,
        "last_unmet": me.last_unmet_upkeep.get(resource),
        "imported_total": me.imported_total.get(resource),
        "exported_total": me.exported_total.get(resource),
        "history": [[tick, inventory.get(resource)] for tick, inventory in history],
    }


def _offer_row(state: model.State, offer: model.Offer) -> dict:
    counterparty, give, get = our_side(offer, state.self_station_id)
    return {
        "offer_id": offer.offer_id,
        "counterparty": counterparty,
        "we_give": bundle_json(give),
        "we_get": bundle_json(get),
        "expires_tick": offer.expires_tick,
        "ticks_left": max(0, offer.expires_tick - state.tick),
    }


def _offers(state: model.State) -> dict:
    # Soonest to expire first: those are the ones worth looking at.
    def urgency(offer):
        return (offer.expires_tick, offer.created_version)

    # A state lists only offers we are party to, with their whole history, so
    # this is how our offers have ended so far -- not the wider market's.
    ended = Counter(o.status.name.removeprefix("OFFER_STATUS_").lower() for o in state.offers)
    return {
        "outgoing": [_offer_row(state, o) for o in sorted(store.my_open_offers(state), key=urgency)],
        "incoming": [_offer_row(state, o) for o in sorted(store.incoming_open_offers(state), key=urgency)],
        "max_outgoing": state.rules.max_open_outgoing_offers,
        "slots_left": store.remaining_offer_slots(state),
        "history": {"total": len(state.offers), **ended},
    }


def _trades(state: model.State, limit: int) -> dict:
    me = state.self_station_id
    mine = [t for t in state.transactions if me in (t.proposer_id, t.recipient_id)]
    mine.sort(key=lambda t: t.settled_version, reverse=True)
    rows = []
    for transaction in mine[:limit]:
        counterparty, gave, got = our_side(transaction, me)
        rows.append(
            {
                "transaction_id": transaction.transaction_id,
                "settled_tick": transaction.settled_tick,
                "counterparty": counterparty,
                "we_proposed": transaction.proposer_id == me,
                "we_gave": bundle_json(gave),
                "we_got": bundle_json(got),
            }
        )
    return {"recent": rows, "total": len(mine)}


def _actions(state: model.State, records, totals, last_decision, limit: int) -> dict:
    newest_first = list(records)[-limit:][::-1]
    return {
        "recent": [
            {
                "world_version": r.world_version,
                "tick": r.tick,
                "description": r.description,
                "reason": r.reason,
                "score": round(r.score, 2),
                "terms": r.terms,
                "status": r.status,
                "request_id": r.request_id,
                "detail": r.detail,
            }
            for r in newest_first
        ],
        "totals": dict(totals or {}),
        "last_decision": last_decision,
        "results_used": len(state.request_results),
        "results_cap": state.rules.max_request_records_per_station,
        "commands_per_tick": state.rules.new_commands_per_station_per_tick,
    }
