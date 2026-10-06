"""One run's ``messages.jsonl``, boiled down to what a history page can chart.

Pure, like ``view.py``: rows in, a plain dict out, no clock and no I/O.  It
reads the log as the JSON it was written as -- no ``model`` types, no
generated bindings, nothing from the rest of ``bazaar`` -- so it keeps working
when this folder lives in its own repo, and on logs written by older clients.
Every field is read defensively: an older log that lacks one gives a zero or a
``None``, never an exception.

The log has no timestamps, so everything here is measured in ticks.

A run ends one of three ways, and all three produce a summary:

* ``finished``    the server reached ``PHASE_FINISHED``;
* ``aborted``     the server ended the run early;
* ``interrupted`` the log simply stops -- Ctrl+C, a dropped connection, a crash.
  The summary covers what was seen, and ``progress`` says how far that got.

Where to look for things:

* What we *own* at the end (trades, offers, totals, health) comes from the last
  state.  A state carries the whole ledger, so it is complete even when the
  run was cut short.
* What we *did* (commands and how they ended) comes from sent messages matched
  to their results by ``request_id``.
* Per-tick curves come from the last state seen in each tick.

The ledger is the *run's*, not the session's: a client restarted mid-game sees
the trades and totals of the earlier session too, while its ``commands`` and
curves cover only what this log saw.  ``result.first_tick`` says where this
log's view begins.
"""

from collections import Counter

SCHEMA_VERSION = 1

#: The bundle's field names, which is how every amount in a log is keyed.
RESOURCES = ("water", "food", "components")

#: How a command can end.  ``errored`` means a protocol error named it;
#: ``unanswered`` means nothing ever did.
COMMAND_OUTCOMES = ("ok", "failed", "errored", "unanswered")


def summarize_session(rows, *, session: str | None = None, context: dict | None = None,
                      skipped_lines: int = 0) -> dict:
    """Summarise a whole log: one entry per run ID found in it.

    A client that stays connected can see the server start a new run, so one log
    may hold several.  ``context`` is anything the caller knows that the log does
    not (which policy was playing); it is copied through untouched.
    ``skipped_lines`` is how many lines the reader could not parse, reported so a
    damaged log is visible rather than silently shorter.
    """
    by_run: dict[str, _Run] = {}
    total = 0
    for row in rows:
        message = row.get("message") if isinstance(row, dict) else None
        if not isinstance(message, dict) or not message.get("run_id"):
            continue
        total += 1
        by_run.setdefault(message["run_id"], _Run(message["run_id"])).add(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "session": session,
        "context": dict(context or {}),
        "log": {"rows": total, "skipped_lines": skipped_lines},
        "runs": [run.finish() for run in by_run.values()],
    }


def _zero() -> dict[str, int]:
    return dict.fromkeys(RESOURCES, 0)


def _bundle(raw) -> dict[str, int]:
    raw = raw or {}
    return {key: int(raw.get(key) or 0) for key in RESOURCES}


def _minus(a: dict, b: dict) -> dict[str, int]:
    return {key: a[key] - b[key] for key in RESOURCES}


def _plus(a: dict, b: dict) -> dict[str, int]:
    return {key: a[key] + b[key] for key in RESOURCES}


def _short(name: str | None, prefix: str) -> str | None:
    return name.removeprefix(prefix) if isinstance(name, str) else name


class _Run:
    """Folds one run's rows in log order, keeping only what the summary needs.

    A state's line holds every offer and result so far, so states are reduced to
    one small point per tick as they go by and only the last is kept whole.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.sent = 0
        self.received = 0
        self.states = 0
        self.last_state: dict | None = None
        self.tick: int | None = None
        self.phases: list[str] = []
        self.points: dict[int, dict] = {}
        #: request_id -> {kind, tick, attempts, outcome, code}
        self.commands: dict[str, dict] = {}
        self.unmatched_errors: Counter[str] = Counter()

    # --- folding ------------------------------------------------------------
    def add(self, row: dict) -> None:
        kind, message = row.get("kind"), row["message"]
        if row.get("direction") == "sent":
            self.sent += 1
            self._command(kind, message)
        else:
            self.received += 1
            if kind == "state":
                self._state(message)
            elif kind == "result":
                self._result(message)
            elif kind == "protocol_error":
                self._protocol_error(message)

    def _state(self, state: dict) -> None:
        self.states += 1
        self.last_state = state
        tick = state.get("tick")
        phase = _short(state.get("phase"), "PHASE_")
        if phase and (not self.phases or self.phases[-1] != phase):
            self.phases.append(phase)
        if not isinstance(tick, int):
            return
        self.tick = tick
        me = state.get("observation") or {}
        # Later states in the same tick overwrite earlier ones: the tick's last word.
        self.points[tick] = {
            "health": me.get("health"),
            "inventory": _bundle(me.get("inventory")),
            "unmet": _bundle(me.get("last_unmet_upkeep")),
        }

    def _command(self, kind: str, message: dict) -> None:
        request_id = message.get("request_id")
        if not request_id:
            return  # ready and sync are not commands: nothing answers them
        known = self.commands.get(request_id)
        if known is not None:
            known["attempts"] += 1  # a retry reuses the ID, and the server acts once
            return
        self.commands[request_id] = {"kind": kind, "tick": self.tick, "attempts": 1,
                                     "outcome": "unanswered", "code": None}

    def _command_for(self, request_id: str | None) -> dict | None:
        if request_id is None:
            return None
        # A result for something we never logged sending: still worth counting.
        return self.commands.setdefault(request_id, {"kind": "unknown", "tick": None, "attempts": 0,
                                                     "outcome": "unanswered", "code": None})

    def _result(self, result: dict) -> None:
        command = self._command_for(result.get("request_id"))
        if command is not None:
            command["outcome"] = "ok" if result.get("ok") else "failed"
            command["code"] = _short(result.get("code"), "RESULT_CODE_")

    def _protocol_error(self, error: dict) -> None:
        code = _short(error.get("code"), "CONTROL_CODE_") or "UNKNOWN"
        command = self._command_for(error.get("request_id"))
        if command is None:
            self.unmatched_errors[code] += 1
            return
        command["outcome"] = "errored"
        command["code"] = code

    # --- the summary --------------------------------------------------------
    def finish(self) -> dict:
        state = self.last_state or {}
        me = state.get("observation") or {}
        rules = state.get("rules") or {}
        station = state.get("self_station_id")
        names = {e.get("station_id"): e.get("display_name") or e.get("station_id")
                 for e in state.get("directory") or []}
        trades = _trades(state, station, names)
        trades_by_tick = trades.pop("per_tick")  # a curve, so it lives in ``series``
        commands_by_tick = Counter(c["tick"] for c in self.commands.values() if c["tick"] is not None)
        failed_by_tick = Counter(c["tick"] for c in self.commands.values()
                                 if c["tick"] is not None and c["outcome"] in ("failed", "errored"))
        ticks = sorted(self.points)
        return {
            "run_id": self.run_id,
            "station": station,
            "station_name": names.get(station, station),
            "stations": [{"id": sid, "name": name} for sid, name in sorted(names.items())],
            "rules": {
                "duration_ticks": rules.get("duration_ticks"),
                "tick_duration_ms": rules.get("tick_duration_ms"),
                "max_health": rules.get("max_health"),
            },
            "result": self._result_block(state, me, rules, ticks),
            "health": _health(me, self.points, rules),
            "totals": {
                name: _bundle(me.get(f"{name}_total"))
                for name in ("produced", "consumed", "unmet", "imported", "exported")
            },
            "trades": trades,
            "offers": _offers(state, station),
            "commands": self._commands_block(),
            "series": {
                "tick": ticks,
                "health": [self.points[t]["health"] for t in ticks],
                "inventory": {key: [self.points[t]["inventory"][key] for t in ticks] for key in RESOURCES},
                "unmet_upkeep": {key: [self.points[t]["unmet"][key] for t in ticks] for key in RESOURCES},
                "trades": [trades_by_tick.get(t, 0) for t in ticks],
                "commands": [commands_by_tick.get(t, 0) for t in ticks],
                "commands_failed": [failed_by_tick.get(t, 0) for t in ticks],
            },
            "messages": {"sent": self.sent, "received": self.received, "states": self.states},
        }

    def _result_block(self, state: dict, me: dict, rules: dict, ticks: list[int]) -> dict:
        outcome = state.get("outcome")
        phase = _short(state.get("phase"), "PHASE_")
        if phase == "ABORTED" or (outcome or {}).get("aborted"):
            status = "aborted"
        elif phase == "FINISHED" or outcome:
            status = "finished"
        else:
            # Nothing says the game ended, so our side of the connection did.
            status = "interrupted"
        duration = rules.get("duration_ticks")
        last = ticks[-1] if ticks else None
        return {
            "status": status,
            "phase": phase,
            "phases_seen": self.phases,
            "first_tick": ticks[0] if ticks else None,
            "last_tick": last,
            "progress": round(last / duration, 3) if last is not None and duration else None,
            "our_station_failed": bool((outcome or {}).get("self_failed") or me.get("failed_once")),
            "collective_success": (outcome or {}).get("collective_success"),
            "outcome": outcome,
        }

    def _commands_block(self) -> dict:
        by_kind: dict[str, Counter] = {}
        failures: Counter[str] = Counter()
        for command in self.commands.values():
            by_kind.setdefault(command["kind"], Counter())[command["outcome"]] += 1
            if command["outcome"] in ("failed", "errored"):
                failures[command["code"] or "UNKNOWN"] += 1
        return {
            "sent": len(self.commands),
            "retried": sum(1 for c in self.commands.values() if c["attempts"] > 1),
            "by_kind": {
                kind: {"sent": sum(counts.values()), **{name: counts[name] for name in COMMAND_OUTCOMES}}
                for kind, counts in sorted(by_kind.items())
            },
            "failures": dict(failures.most_common()),
            "unmatched_protocol_errors": dict(self.unmatched_errors),
        }


def _health(me: dict, points: dict[int, dict], rules: dict) -> dict:
    seen = [p["health"] for p in points.values() if p["health"] is not None]
    return {
        "final": me.get("health"),
        "min": min(seen) if seen else None,
        "max": rules.get("max_health"),
        "failed_once": bool(me.get("failed_once")),
        "first_failure_tick": me.get("first_failure_tick"),
        "shortage_ticks": me.get("shortage_ticks") or 0,
        "longest_shortage_streak": me.get("longest_shortage_streak") or 0,
        "fully_supplied_ticks": me.get("fully_supplied_ticks") or 0,
    }


def _trades(state: dict, station: str | None, names: dict) -> dict:
    """Our settled trades, from our side.

    A transaction records amounts from the proposer's point of view, so what we
    gave is ``give`` only if we proposed it.
    """
    gave, got = _zero(), _zero()
    proposed = 0
    per_tick: Counter[int] = Counter()
    peers: dict[str, dict] = {}
    for trade in state.get("transactions") or []:
        if station not in (trade.get("proposer_id"), trade.get("recipient_id")):
            continue
        we_proposed = trade.get("proposer_id") == station
        peer = trade.get("recipient_id") if we_proposed else trade.get("proposer_id")
        ours = (trade.get("give"), trade.get("receive")) if we_proposed else (trade.get("receive"), trade.get("give"))
        paid, received = _bundle(ours[0]), _bundle(ours[1])
        gave, got = _plus(gave, paid), _plus(got, received)
        proposed += we_proposed
        if isinstance(trade.get("settled_tick"), int):
            per_tick[trade["settled_tick"]] += 1
        row = peers.setdefault(peer, {"station": peer, "name": names.get(peer, peer), "count": 0,
                                      "gave": _zero(), "got": _zero()})
        row["count"] += 1
        row["gave"], row["got"] = _plus(row["gave"], paid), _plus(row["got"], received)
    count = sum(row["count"] for row in peers.values())
    by_counterparty = sorted(peers.values(), key=lambda row: (-row["count"], row["station"]))
    for row in by_counterparty:
        row["net"] = _minus(row["got"], row["gave"])
    return {
        "count": count,
        "we_proposed": proposed,
        "we_accepted": count - proposed,
        "gave": gave,
        "got": got,
        "net": _minus(got, gave),
        "by_counterparty": by_counterparty,
        "per_tick": dict(per_tick),
    }


def _offers(state: dict, station: str | None) -> dict:
    """How the offers *we made* ended.  A state lists only offers we are party to."""
    mine = [o for o in state.get("offers") or [] if o.get("proposer_id") == station]
    by_status = Counter(_short(o.get("status"), "OFFER_STATUS_").lower() for o in mine if o.get("status"))
    made = len(mine)
    return {
        "made": made,
        "by_status": dict(sorted(by_status.items())),
        "acceptance_rate": round(by_status["accepted"] / made, 3) if made else None,
    }
