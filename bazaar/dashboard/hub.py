"""The dashboard's memory of the run, fed by the transport and the runner.

A state says what *is*; it does not say what we tried.  The action lifecycle --
decided, sent, answered -- exists only on our side of the socket, so the runner
reports it here as it happens.  This is the one stateful piece of the
dashboard; what the page shows is computed by ``view.render``.

Every hook swallows its own errors.  A display must never be the reason a trade
did not go out, so a failure is counted and shown on the page instead.
"""

import functools
import json
import time
from collections import Counter, deque

from .. import config
from ..validation import model
from .view import ActionRecord, action_terms, render


def _guarded(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self.errors += 1
            self.last_error = f"{method.__name__}: {type(exc).__name__}: {exc}"
            return None

    return wrapper


class Dashboard:
    """Remembers what the page needs and tells subscribers when it changes.

    Subscribers are called with the page's JSON as a string; the server is the
    only one in practice.  A dashboard nobody subscribes to still records
    everything, which is how the tests use it.
    """

    def __init__(self, settings: config.DashboardSettings = config.DASHBOARD, clock=time.time):
        self.settings = settings
        self._clock = clock
        self._subscribers = []

        self.state: model.State | None = None
        #: ``(tick, inventory)`` pairs, one per tick, oldest first.
        self.history: deque[tuple[int, model.Bundle]] = deque(maxlen=settings.history_ticks)
        self.actions: deque[ActionRecord] = deque(maxlen=settings.recent_actions)
        #: Running counts of each status reached, over the whole run.
        self.totals: Counter[str] = Counter()
        self.last_decision: dict | None = None
        #: Wall-clock time we first saw the current tick, for the countdown.
        self.tick_seen_at_ms: float | None = None

        self.errors = 0
        self.last_error: str | None = None

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    # --- what the page shows ----------------------------------------------
    def view(self) -> dict:
        page = {"state": None} if self.state is None else render(
            self.state,
            history=self.history,
            actions=self.actions,
            totals=self.totals,
            last_decision=self.last_decision,
            tick_seen_at_ms=self.tick_seen_at_ms,
            settings=self.settings,
        )
        page["dashboard"] = {"errors": self.errors, "last_error": self.last_error}
        return page

    def payload(self) -> str:
        """The view as JSON, or a stub naming the failure if it cannot render."""
        try:
            return json.dumps(self.view())
        except Exception as exc:
            self.errors += 1
            self.last_error = f"view: {type(exc).__name__}: {exc}"
            return json.dumps({"state": None, "dashboard": {"errors": self.errors, "last_error": self.last_error}})

    def publish(self) -> None:
        if not self._subscribers:
            return
        payload = self.payload()
        for callback in self._subscribers:
            try:
                callback(payload)
            except Exception as exc:
                self.errors += 1
                self.last_error = f"publish: {type(exc).__name__}: {exc}"

    # --- hooks: the transport -----------------------------------------------
    @_guarded
    def on_state(self, state: model.State) -> None:
        previous = self.state
        self.state = state
        if previous is None or previous.tick != state.tick:
            self.tick_seen_at_ms = self._clock() * 1000
        if self.history and state.tick < self.history[-1][0]:
            self.history.clear()  # a new run; the old curve means nothing now
        point = (state.tick, state.observation.inventory)
        if self.history and self.history[-1][0] == state.tick:
            self.history[-1] = point
        else:
            self.history.append(point)
        self.publish()

    # --- hooks: the runner --------------------------------------------------
    @_guarded
    def decided(self, state: model.State, actions) -> None:
        # Anything still queued from an earlier decision was never sent.
        for record in self.actions:
            if record.status == "queued":
                self._settle(record, "skipped")
        for action in actions:
            self.actions.append(
                ActionRecord(
                    action=action,
                    world_version=state.world_version,
                    tick=state.tick,
                    description=action.describe(),
                    reason=action.reason,
                    score=action.score,
                    terms=action_terms(action, state),
                )
            )
        self.last_decision = {
            "world_version": state.world_version,
            "tick": state.tick,
            "count": len(actions),
        }
        self.publish()

    @_guarded
    def sent(self, action, request_id: str) -> None:
        record = self._by_action(action)
        if record is not None:
            record.request_id = request_id
            self._settle(record, "sent")
            self.publish()

    @_guarded
    def resolved(self, request_id: str, result: model.Result) -> None:
        record = self._by_request(request_id)
        if record is not None:
            record.detail = result.code.name.removeprefix("RESULT_CODE_")
            self._settle(record, "ok" if result.ok else "failed")
            self.publish()

    @_guarded
    def rejected(self, action, reason: str) -> None:
        record = self._by_action(action)
        if record is not None:
            record.detail = reason
            self._settle(record, "rejected")
            self.publish()

    @_guarded
    def errored(self, request_id: str, message: str) -> None:
        record = self._by_request(request_id)
        if record is not None:
            record.detail = message
            self._settle(record, "error")
            self.publish()

    # --- bookkeeping --------------------------------------------------------
    def _settle(self, record: ActionRecord, status: str) -> None:
        record.status = status
        self.totals[status] += 1

    def _by_action(self, action) -> ActionRecord | None:
        # Identity, not equality: two identical offers are two records.
        for record in reversed(self.actions):
            if record.action is action and record.status == "queued":
                return record
        return None

    def _by_request(self, request_id: str) -> ActionRecord | None:
        for record in reversed(self.actions):
            if record.request_id == request_id:
                return record
        return None
