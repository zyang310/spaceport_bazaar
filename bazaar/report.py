"""Tells an external dashboard what the agent decided and why, if one is listening.

The dashboard lives in its own directory (``bazaar-dashboard/``) and is
connected to this client by its own setup, which installs a tap in this
interpreter.  The tap copies every frame on the socket to it, so commands and
results reach the page with no help from here.  What no frame carries is the
agent's side: what it decided, its reason and score, and what it decided but
never sent.  This module reports exactly that, through the tap's ``report``.

Without the tap -- the dashboard never set up, or this process not the one it
watches -- :func:`reporter` returns ``None`` and the runner reports to nobody.
Like every display hook, each call swallows its own errors: reporting must
never be the reason a command did not go out.
"""

import functools

from . import actions

try:
    import bazaar_dashboard_tap as _tap
except ImportError:
    _tap = None


def _quiet(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            method(self, *args, **kwargs)
        except Exception:
            pass

    return wrapper


def command_spec(action: actions.Action) -> dict:
    """An action's terms in the plain form the dashboard reads; see its README."""
    if isinstance(action, actions.Advertise):
        return {"kind": "advertise", "selling": [r.name for r in action.selling],
                "seeking": [r.name for r in action.seeking], "expires_tick": action.expires_tick}
    if isinstance(action, actions.Offer):
        return {"kind": "offer", "recipient_id": action.recipient_id, "give": list(action.give),
                "receive": list(action.receive), "expires_tick": action.expires_tick}
    if isinstance(action, actions.Accept):
        return {"kind": "accept", "offer_id": action.offer_id}
    if isinstance(action, actions.Withdraw):
        return {"kind": "withdraw", "object_id": action.object_id}
    return {"kind": type(action).__name__.lower()}


class DashboardReporter:
    """The runner's ``activity``, passed on to the dashboard as reports.

    The runner names actions by the objects themselves; the dashboard, in
    another process, needs a key.  Each action gets one when it is decided,
    and keeps it while the runner still holds the batch, which is as long as
    any later call can mention it.
    """

    def __init__(self, send):
        self._send = send
        self._keys: dict[int, int] = {}
        self._next_key = 1

    @_quiet
    def decided(self, state, chosen) -> None:
        self._keys = {}
        entries = []
        for action in chosen:
            key, self._next_key = self._next_key, self._next_key + 1
            self._keys[id(action)] = key
            entries.append({"key": key, "description": action.describe(), "reason": action.reason,
                            "score": action.score, "command": command_spec(action)})
        self._send("decided", world_version=state.world_version, tick=state.tick, actions=entries)

    @_quiet
    def sent(self, action, request_id: str) -> None:
        key = self._keys.get(id(action))
        if key is not None:
            self._send("sent", key=key, request_id=request_id)

    def resolved(self, request_id, result) -> None:
        """Nothing to say: the result frame reaches the dashboard by itself."""

    @_quiet
    def rejected(self, action, reason: str) -> None:
        key = self._keys.get(id(action))
        if key is not None:
            self._send("rejected", key=key, detail=reason)

    @_quiet
    def errored(self, request_id: str, message: str) -> None:
        self._send("errored", request_id=request_id, detail=message)


def reporter(agent: str, tap=None) -> DashboardReporter | None:
    """A reporter if a dashboard tap is active in this process, else ``None``."""
    tap = tap if tap is not None else _tap
    try:
        if tap is None or not tap.active():
            return None
        tap.report("agent", name=agent)
        return DashboardReporter(tap.report)
    except Exception:
        return None
