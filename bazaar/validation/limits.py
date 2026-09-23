"""Legality: is this command allowed to be sent at all?

Every command passes through :func:`validate_command` on its way to the socket,
so a malformed or out-of-bounds command fails here -- with a message naming the
rule it broke -- instead of costing a round trip and a stored result slot.

This is deliberately separate from ``decode.py``.  Translation is mechanical and
depends on nothing; validation needs the run's rules and our current state.
Mixing them makes both harder to test.

Legality is not judgement: whether a command is a *good idea* is
``brain/policy/``'s question, not this file's.
"""

import re

from . import encode, model

#: The guide's rule: 1-64 letters, digits, underscores, or hyphens.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Arms that carry a request_id.  ``sync`` and ``ready`` genuinely have none.
_ARMS_WITH_REQUEST_ID = ("advertise", "offer", "accept", "withdraw")

#: Arms that carry an expires_tick, and the rule that bounds it.
_TTL_RULES = {
    "advertise": "max_publication_ttl_ticks",
    "offer": "max_offer_ttl_ticks",
}


class CommandRejected(ValueError):
    """Raised when a command breaks a rule we can check before sending."""


def check_request_id(request_id: str) -> None:
    if not REQUEST_ID_PATTERN.match(request_id):
        raise CommandRejected(
            f"request_id {request_id!r} is not 1-64 letters, digits, underscores, or hyphens"
        )


def check_expires_tick(expires_tick: int, state: model.State, ttl_rule: str) -> None:
    """An expiry must be in the future and within the rule's ceiling.

    ``expires_tick`` is a tick, not a number of seconds, and the ceiling is
    measured from the current tick.
    """
    if expires_tick <= state.tick:
        raise CommandRejected(
            f"expires_tick {expires_tick} must be greater than the current tick {state.tick}"
        )
    ceiling = state.tick + getattr(state.rules, ttl_rule)
    if expires_tick > ceiling:
        raise CommandRejected(
            f"expires_tick {expires_tick} is beyond {ttl_rule} ceiling {ceiling}"
        )


def validate_command(message: model.ClientMessage, state: model.State) -> bytes:
    """Check one command against the rules, returning the bytes to send.

    Serializing is itself a check: a missing required field raises here rather
    than producing a frame the server would answer with
    ``CONTROL_CODE_BAD_MESSAGE``.  The bytes are returned so the caller does not
    have to encode a second time.
    """
    arm = message.which()
    command = message.inner()

    if arm in _ARMS_WITH_REQUEST_ID:
        check_request_id(command.request_id)

    if arm in _TTL_RULES:
        check_expires_tick(command.body.expires_tick, state, _TTL_RULES[arm])

    try:
        raw = encode.encode_bytes(message)
    except Exception as exc:  # EncodeError, and anything else the bindings raise
        raise CommandRejected(f"{arm} command could not be serialized: {exc}") from exc

    limit = state.rules.max_command_bytes
    if len(raw) > limit:
        raise CommandRejected(
            f"{arm} command is {len(raw)} bytes, over the {limit}-byte limit"
        )

    return raw
