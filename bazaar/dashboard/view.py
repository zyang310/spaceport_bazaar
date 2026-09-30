"""What the dashboard shows, computed from one state as plain JSON.

Pure, like a policy: no clock, no I/O, no memory of its own.  Everything that
needs remembering -- inventory over time, the actions we tried -- is handed in
by ``hub.Dashboard``, which is what lets a captured fixture render exactly as
it would live.

The one interpretive step is :func:`our_side`.  Offers and transactions record
amounts from the *proposer's* point of view, so "give 2 water" means we pay two
water only when we proposed it.  Every row the page shows goes through that one
function, so the direction of a deal is decided in exactly one place.

The agent sends commands faster than anyone can read them, so the page leads
with things that change slowly -- what is open, what the agent is after, counts
over the last few ticks -- and folds repeats into one line.  The raw action
list is still here, behind the page's "full action log".
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


def reserve_level(me: model.StationObservation, resource: model.Resource,
                  settings: config.DashboardSettings) -> tuple[float | None, bool, str]:
    """``(cover_ticks, self_sufficient, level)`` for one resource.

    A resource we make at least as fast as we burn cannot run short, however
    little of it is in the hold -- an agent that sells its specialty as fast as
    it is made keeps that stock near zero on purpose -- so it is never low.
    """
    upkeep = me.upkeep_per_tick.get(resource)
    cover = cover_ticks(me.inventory.get(resource), upkeep)
    self_sufficient = upkeep > 0 and me.last_production.get(resource) >= upkeep
    return cover, self_sufficient, "ok" if self_sufficient else cover_level(cover, settings)


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


#: Plain words for why a command failed, by result code, plus our own two
#: markers for commands that never got a result; see :func:`failure_key`.
FAILURE_WORDS = {
    "RATE_LIMITED": "rate limited",
    "LIMIT_REACHED": "offer slots full",
    "NOT_OPEN": "offer already taken",
    "NOT_FOUND": "offer not found",
    "EXPIRED": "expired before it arrived",
    "INSUFFICIENT_RESOURCES": "not enough stock",
    "INVALID_ARGUMENT": "invalid command",
    "REQUEST_ID_CONFLICT": "request ID clash",
    "RUN_NOT_RUNNING": "run not running",
    "STATION_FAILED": "station failed",
    "REJECTED_LOCALLY": "blocked before sending",
    "PROTOCOL_ERROR": "protocol error",
}

#: The statuses a command ends in when it did not do what the agent wanted.
FAILED_STATUSES = ("failed", "rejected", "error")


def failure_key(status: str, detail: str | None) -> str:
    """What a failed command is counted under.

    A result carries a code; a command we refused to send, or one the server
    answered with a protocol error, has only a message, so those two are
    counted under our own markers rather than one bucket per message.
    """
    if status == "rejected":
        return "REJECTED_LOCALLY"
    if status == "error":
        return "PROTOCOL_ERROR"
    return detail or "UNKNOWN"


def failure_words(key: str) -> str:
    return FAILURE_WORDS.get(key, key.replace("_", " ").lower())


def render(
    state: model.State,
    *,
    history=(),
    actions=(),
    totals: dict | None = None,
    last_decision: dict | None = None,
    tick_seen_at_ms: float | None = None,
    events=(),
    failures: dict | None = None,
    agent: str | None = None,
    settings: config.DashboardSettings = config.DASHBOARD,
) -> dict:
    """The whole page's data for one state.

    ``history`` is ``(tick, Bundle)`` pairs, oldest first.  ``actions`` is
    :class:`ActionRecord` objects, oldest first.  ``events`` are what the hub
    noticed between states (see :func:`notable_changes`), oldest first, and
    ``failures`` maps the tick a command was decided in to a ``Counter`` of
    :func:`failure_key` values.
    """
    failures = failures or {}
    reserves = [_reserve(state, r, history, settings) for r in state.rules.resource_order]
    offers = _offers(state)
    return {
        "header": _header(state, tick_seen_at_ms, agent),
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
        "stations": _stations(state, settings),
        "map": {"cargo_ticks": settings.cargo_ticks},
        "plan": plan(state, reserves, offers),
        "recent": _recent(state, failures, settings),
        "highlights": highlights(events, failures, settings),
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


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _and(words: list[str]) -> str:
    """``["a", "b", "c"]`` -> ``"a, b and c"``."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _amounts(bundle: model.Bundle, sign: str = "") -> str:
    parts = [f"{sign}{n} {key}" for key, n in bundle_json(bundle).items() if n]
    return _and(parts) if parts else "nothing"


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

    consumed = sorted((r for r in reserves if r["cover_ticks"] is not None and not r["self_sufficient"]),
                      key=lambda r: r["cover_ticks"])
    if not consumed:
        return {"tone": "good", "title": "All reserves are comfortable",
                "body": "We make as much as we burn of everything we use."}
    worst = consumed[0]
    if worst["level"] == "ok":
        return {"tone": "good", "title": "All reserves are comfortable",
                "body": f"The tightest is {worst['resource']}, with {_ticks(worst['cover_ticks'])} left."}

    line = settings.critical_cover_ticks if worst["level"] == "critical" else settings.low_cover_ticks
    left = "None left" if worst["cover_ticks"] == 0 else f"{_ticks(worst['cover_ticks'])} left"
    body = f"{left}, under the {line:g}-tick line."
    others = [f"{r['resource']} ({r['cover_ticks']:.1f})" for r in consumed[1:] if r["level"] != "ok"]
    if others:
        body += f" Also low: {', '.join(others)}."
    title = f"{worst['resource'].capitalize()} is {'nearly out' if worst['level'] == 'critical' else 'running low'}"
    return {"tone": "critical" if worst["level"] == "critical" else "warning", "title": title,
            "body": body + helpers(worst["resource"])}


def _header(state: model.State, tick_seen_at_ms: float | None, agent: str | None) -> dict:
    outcome = state.outcome
    me = state.observation
    return {
        "station": state.self_station_id,
        "names": {entry.station_id: entry.display_name for entry in state.directory},
        "agent": agent,
        "specialty": resource_key(me.specialty),
        "specialty_made": me.last_production.get(me.specialty),
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
    upkeep = me.upkeep_per_tick.get(resource)
    cover, self_sufficient, level = reserve_level(me, resource, settings)
    return {
        "resource": resource_key(resource),
        "amount": me.inventory.get(resource),
        "upkeep": upkeep,
        "cover_ticks": None if cover is None else round(cover, 1),
        "self_sufficient": self_sufficient,
        "level": level,
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


def _soonest_first(offer: model.Offer) -> tuple[int, int]:
    # Soonest to expire first: those are the ones worth looking at.
    return (offer.expires_tick, offer.created_version)


def _offers(state: model.State) -> dict:
    # A state lists only offers we are party to, with their whole history, so
    # this is how our offers have ended so far -- not the wider market's.
    ended = Counter(o.status.name.removeprefix("OFFER_STATUS_").lower() for o in state.offers)
    return {
        "outgoing": [_offer_row(state, o) for o in sorted(store.my_open_offers(state), key=_soonest_first)],
        "incoming": [_offer_row(state, o) for o in sorted(store.incoming_open_offers(state), key=_soonest_first)],
        "max_outgoing": state.rules.max_open_outgoing_offers,
        "slots_left": store.remaining_offer_slots(state),
        "history": {"total": len(state.offers), **ended},
    }


def _our_trades(state: model.State) -> list[model.Transaction]:
    """Our settled trades, oldest first."""
    me = state.self_station_id
    mine = [t for t in state.transactions if me in (t.proposer_id, t.recipient_id)]
    return sorted(mine, key=lambda t: t.settled_version)


def _trade_row(transaction: model.Transaction, me: str) -> dict:
    counterparty, gave, got = our_side(transaction, me)
    return {
        "transaction_id": transaction.transaction_id,
        "settled_tick": transaction.settled_tick,
        "counterparty": counterparty,
        "we_proposed": transaction.proposer_id == me,
        "we_gave": bundle_json(gave),
        "we_got": bundle_json(got),
    }


def _trades(state: model.State, limit: int) -> dict:
    mine = _our_trades(state)
    newest_first = mine[::-1][:limit]
    return {"recent": [_trade_row(t, state.self_station_id) for t in newest_first], "total": len(mine)}


def _stations(state: model.State, settings: config.DashboardSettings) -> list[dict]:
    """Every other station as the map draws it: what it lists, and what is open
    or recently settled between it and us, in station-ID order.

    The directory names the stations.  Anyone we have dealt with whom the
    directory leaves out is added rather than dropped, so no lane leads nowhere.
    """
    me = state.self_station_id
    names = {entry.station_id: entry.display_name for entry in state.directory}
    listings = {ad.station_id: ad for ad in store.peer_active_advertisements(state)}
    incoming = sorted(store.incoming_open_offers(state), key=_soonest_first)
    outgoing = sorted(store.my_open_offers(state), key=_soonest_first)
    trades = _our_trades(state)

    ids = set(names) | {o.proposer_id for o in incoming} | {o.recipient_id for o in outgoing}
    ids |= {our_side(t, me)[0] for t in trades}
    ids.discard(me)

    rows = []
    for station_id in sorted(ids):
        listing = listings.get(station_id)
        theirs = [t for t in trades if station_id in (t.proposer_id, t.recipient_id)]
        last = theirs[-1] if theirs else None
        rows.append(
            {
                "station": station_id,
                "name": names.get(station_id, station_id),
                "sells": [resource_key(r) for r in listing.selling] if listing else [],
                "seeks": [resource_key(r) for r in listing.seeking] if listing else [],
                "incoming": [_offer_row(state, o) for o in incoming if o.proposer_id == station_id],
                "outgoing": [_offer_row(state, o) for o in outgoing if o.recipient_id == station_id],
                "trades_total": len(theirs),
                "last_trade": None if last is None else _trade_row(last, me),
                # Cargo is still in flight from a trade this recent.
                "cargo": last is not None and state.tick - last.settled_tick < settings.cargo_ticks,
            }
        )
    return rows


def plan(state: model.State, reserves: list[dict], offers: dict) -> dict:
    """What the agent is after, read off what it has open right now.

    An offer stays out for several ticks, so intent changes slowly enough to
    follow, where the stream of commands behind it does not.  One goal per
    resource, in the rules' order: ``buy`` while we have offers out for it or
    list it as wanted, ``sell`` while we pay with it or list it for sale, and
    ``hold`` otherwise.
    """
    if state.phase is not model.Phase.PHASE_RUNNING:
        return {"headline": PHASE_HEADLINES.get(state.phase, "Not running"), "goals": []}

    listing = store.my_active_advertisement(state)
    selling = {resource_key(r) for r in listing.selling} if listing else set()
    seeking = {resource_key(r) for r in listing.seeking} if listing else set()
    goals = []
    for reserve in reserves:
        key = reserve["resource"]
        buying = [o for o in offers["outgoing"] if o["we_get"][key]]
        paying = [o for o in offers["outgoing"] if o["we_give"][key]]
        offered = [o for o in offers["incoming"] if o["we_get"][key]]
        if buying or key in seeking:
            verb = "buy"
            parts = [f"{_count(len(buying), 'offer')} out" if buying else "listed as wanted"]
        elif paying or key in selling:
            verb = "sell"
            parts = ["listed"] if key in selling else []
            if paying:
                parts.append(f"paying with it in {_count(len(paying), 'offer')}")
        else:
            verb = "hold"
            cover = reserve["cover_ticks"]
            if reserve["self_sufficient"]:
                parts = ["we make as much as we burn"]
            else:
                parts = ["not consumed" if cover is None else f"{_ticks(cover)} left"]
        if offered and verb != "sell":
            first = offered[0]
            parts.append(f"{first['counterparty']} offering {first['we_get'][key]} {key}")
        text = ", ".join(parts)
        urgency = "critical" if reserve["last_unmet"] else {"low": "warning", "critical": "critical"}.get(reserve["level"])
        goals.append({"resource": key, "verb": verb, "text": text[:1].upper() + text[1:], "urgency": urgency})

    buys = [g for g in goals if g["verb"] == "buy"]
    sells = [g["resource"] for g in goals if g["verb"] == "sell"]
    if buys:
        urgent = any(g["urgency"] == "critical" for g in buys)
        headline = ("Urgently buying " if urgent else "Buying ") + _and([g["resource"] for g in buys])
        if sells:
            headline += f" with {_and(sells)}"
    elif sells:
        headline = f"Selling {_and(sells)}"
    else:
        headline = "Holding: nothing to trade"
    return {"headline": headline, "goals": goals}


def _failure_hint(key: str, rules: model.PublicRules) -> str:
    return {
        "RATE_LIMITED": f"more than {rules.new_commands_per_station_per_tick} commands in one tick",
        "LIMIT_REACHED": f"all {rules.max_open_outgoing_offers} offer slots in use",
        "NOT_OPEN": "another station took it first",
        "INSUFFICIENT_RESOURCES": "we could not pay for it",
        "EXPIRED": "it ran out before it arrived",
    }.get(key, "")


def _recent(state: model.State, failures: dict, settings: config.DashboardSettings) -> dict:
    """Counts over the last few ticks, and a longer per-tick strip for the eye.

    Trades and expiries come from the state; failures are ours alone to know,
    so they come from the hub, counted by the tick each command was decided in.
    """
    now, me = state.tick, state.self_station_id
    settled = Counter(t.settled_tick for t in _our_trades(state))
    start = max(0, now - settings.recent_window_ticks + 1)
    reasons = Counter()
    for tick in range(start, now + 1):
        reasons.update(failures.get(tick, {}))
    expired = sum(
        1
        for o in state.offers
        if o.proposer_id == me
        and o.status is model.OfferStatus.OFFER_STATUS_EXPIRED
        and start <= (o.closed_tick if o.closed_tick is not None else o.expires_tick) <= now
    )
    top = None
    if reasons:
        key, count = reasons.most_common(1)[0]
        top = {"reason": failure_words(key), "hint": _failure_hint(key, state.rules), "count": count}
    strip_start = max(0, now - settings.strip_ticks + 1)
    return {
        "from_tick": start,
        "to_tick": now,
        "trades": sum(settled[tick] for tick in range(start, now + 1)),
        "expired": expired,
        "failures": sum(reasons.values()),
        "top_failure": top,
        "strip": [
            {"tick": tick, "trades": settled[tick], "failures": sum(failures.get(tick, {}).values())}
            for tick in range(strip_start, now + 1)
        ],
    }


def _net(got: Counter, gave: Counter) -> str:
    """What a batch of trades did to the hold, e.g. ``+3 water and −7 components``."""
    changes = [(key, got[key] - gave[key]) for key in ("water", "food", "components")]
    parts = [f"{'+' if n > 0 else '−'}{abs(n)} {key}" for key, n in changes if n]
    return _and(parts) if parts else "no net change"


def highlights(events, failures: dict, settings: config.DashboardSettings) -> list[dict]:
    """The few things worth a person's attention, newest first.

    Repeats are folded so a busy agent still reads at a human pace: the trades
    of one tick become one line, the failures of one tick become one line --
    thirty rate-limited commands are one problem, not thirty -- and a reserve
    wobbling across a line is reported once, at its latest.  Each item is
    ``{tick, kind, text}``, where ``kind`` is ``trade``, ``info``, ``warning``,
    ``alert`` or ``failure``.
    """
    items = []
    trades_by_tick: dict[int, list[tuple[int, dict]]] = {}
    for n, event in enumerate(events):
        if event["kind"] == "trade" and "we_got" in event:
            trades_by_tick.setdefault(event["tick"], []).append((n, event))
        else:
            items.append((event["tick"], n, event))
    for tick, trades in trades_by_tick.items():
        newest, event = trades[-1]
        if len(trades) > 1:
            got, gave = Counter(), Counter()
            for _, trade in trades:
                got.update(trade["we_got"])
                gave.update(trade["we_gave"])
            # Netted, because one tick can both pay and receive the same resource.
            event = {"tick": tick, "kind": "trade", "text": f"{len(trades)} trades: {_net(got, gave)}"}
        items.append((tick, newest, event))
    for tick, reasons in failures.items():
        total = sum(reasons.values())
        if not total:
            continue
        key, count = reasons.most_common(1)[0]
        link = ":" if count == total else ", mostly"
        text = f"{_count(total, 'command')} failed{link} {failure_words(key)}"
        items.append((tick, len(events) + tick, {"tick": tick, "kind": "failure", "text": text}))
    items.sort(key=lambda item: (item[0], item[1]), reverse=True)

    shown, said = [], set()
    for _, _, event in items:
        if event["kind"] in ("info", "warning", "alert"):
            if event["text"] in said:
                continue
            said.add(event["text"])
        shown.append({"tick": event["tick"], "kind": event["kind"], "text": event["text"]})
        if len(shown) == settings.highlights:
            break
    return shown


def _levels(state: model.State, settings: config.DashboardSettings) -> dict[model.Resource, str]:
    return {resource: reserve_level(state.observation, resource, settings)[2] for resource in state.rules.resource_order}


def _listing(state: model.State) -> tuple | None:
    ad = store.my_active_advertisement(state)
    return None if ad is None else (tuple(ad.selling), tuple(ad.seeking))


def notable_changes(previous: model.State | None, state: model.State,
                    settings: config.DashboardSettings) -> list[dict]:
    """What changed between two states that a person would want to hear about.

    The hub calls this on every state and keeps the results; they become the
    page's highlights.  Nothing is reported against the first state of a run:
    there is nothing to compare it with.
    """
    if previous is None or previous.run_id != state.run_id:
        return []
    me, tick = state.self_station_id, state.tick
    events = []

    known = {t.transaction_id for t in previous.transactions}
    for transaction in _our_trades(state):
        if transaction.transaction_id in known:
            continue
        counterparty, gave, got = our_side(transaction, me)
        if got.is_zero():
            text = f"Gave {_amounts(gave)} to {counterparty}"
        elif gave.is_zero():
            text = f"{_amounts(got, '+')} from {counterparty}, as a gift"
        else:
            text = f"{_amounts(got, '+')} from {counterparty}, for {_amounts(gave)}"
        # The amounts ride along so the highlights can fold a tick's trades into one line.
        events.append({"tick": transaction.settled_tick, "kind": "trade", "text": text, "counterparty": counterparty,
                       "we_gave": bundle_json(gave), "we_got": bundle_json(got)})

    before, after = _levels(previous, settings), _levels(state, settings)
    for resource in state.rules.resource_order:
        name = resource_key(resource).capitalize()
        ran_out = state.observation.last_unmet_upkeep.get(resource) and not previous.observation.last_unmet_upkeep.get(resource)
        if ran_out:
            events.append({"tick": tick, "kind": "alert", "text": f"{name} ran out: health is falling"})
        elif before[resource] != after[resource]:
            if after[resource] == "critical":
                events.append({"tick": tick, "kind": "alert", "text": f"{name} under {settings.critical_cover_ticks:g} ticks"})
            elif after[resource] == "low" and before[resource] == "ok":
                events.append({"tick": tick, "kind": "warning", "text": f"{name} under {settings.low_cover_ticks:g} ticks"})
            else:
                line = settings.low_cover_ticks if after[resource] == "ok" else settings.critical_cover_ticks
                events.append({"tick": tick, "kind": "info", "text": f"{name} back above {line:g} ticks"})

    was, now = previous.observation, state.observation
    if was.health > 0 >= now.health:
        events.append({"tick": tick, "kind": "alert", "text": "Our station failed"})
    elif was.current_shortage_streak and not now.current_shortage_streak:
        events.append({"tick": tick, "kind": "info", "text": "Fully supplied again: health recovering"})

    # Only a change of what we list is news.  A listing that lapses and is put
    # back unchanged is the agent refreshing it, which happens every few ticks.
    before_listing, after_listing = _listing(previous), _listing(state)
    if before_listing is not None and after_listing is not None and before_listing != after_listing:
        selling, seeking = ([resource_key(r) for r in side] for side in after_listing)
        events.append({"tick": tick, "kind": "info",
                       "text": f"Now selling {_and(selling) if selling else 'nothing'}, "
                               f"seeking {_and(seeking) if seeking else 'nothing'}"})
    return events


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
