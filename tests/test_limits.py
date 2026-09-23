"""The pre-send validator: request-ID format, expiry bounds, and size."""

import pytest

from bazaar.validation import model
from bazaar.validation.commands import CommandBuilder
from bazaar.validation.limits import CommandRejected, validate_command

from .factories import COMPONENTS, FOOD, WATER, rules, state

RUN = "run-1"
B = CommandBuilder(RUN)


def test_a_valid_advertisement_passes_and_returns_its_bytes():
    raw = validate_command(B.advertise("student-advertise-1", [WATER], [FOOD], 6), state())
    assert isinstance(raw, bytes) and raw


@pytest.mark.parametrize(
    "request_id",
    ["", "has spaces", "has.dot", "a" * 65, "emoji-\U0001f600", "slash/es"],
)
def test_bad_request_ids_are_rejected(request_id):
    with pytest.raises(CommandRejected, match="request_id"):
        validate_command(B.advertise(request_id, [WATER], [FOOD], 6), state())


@pytest.mark.parametrize("request_id", ["a", "a" * 64, "student-advertise-1", "A_b-9"])
def test_good_request_ids_are_accepted(request_id):
    validate_command(B.advertise(request_id, [WATER], [FOOD], 6), state())


def test_an_expiry_at_or_before_the_current_tick_is_rejected():
    now = state(tick=4)
    for expires in (0, 3, 4):
        with pytest.raises(CommandRejected, match="greater than the current tick"):
            validate_command(B.advertise("r1", [WATER], [FOOD], expires), now)


def test_an_expiry_beyond_the_publication_ceiling_is_rejected():
    now = state(tick=2, rules=rules(max_publication_ttl_ticks=5))
    validate_command(B.advertise("r1", [WATER], [FOOD], 7), now)  # exactly the ceiling
    with pytest.raises(CommandRejected, match="max_publication_ttl_ticks"):
        validate_command(B.advertise("r1", [WATER], [FOOD], 8), now)


def test_offers_are_bounded_by_the_offer_ceiling_not_the_publication_one():
    now = state(tick=0, rules=rules(max_offer_ttl_ticks=3, max_publication_ttl_ticks=20))
    give, receive = model.Bundle(water=2), model.Bundle(food=1)
    validate_command(B.offer("r1", "P02", give, receive, 3), now)
    with pytest.raises(CommandRejected, match="max_offer_ttl_ticks"):
        validate_command(B.offer("r1", "P02", give, receive, 4), now)


def test_an_oversize_command_is_rejected():
    tiny = state(rules=rules(max_command_bytes=4))
    with pytest.raises(CommandRejected, match="over the 4-byte limit"):
        validate_command(B.advertise("r1", [WATER], [FOOD], 6), tiny)


def test_a_command_that_cannot_serialize_is_rejected_before_sending():
    broken = model.ClientMessage(
        accept=model.Accept(
            type=model.AcceptType.ACCEPT_TYPE_ACCEPT,
            protocol_version="2.0",
            run_id=RUN,
            request_id="r1",
            body=None,
        )
    )
    with pytest.raises(CommandRejected, match="could not be serialized"):
        validate_command(broken, state())


def test_sync_and_ready_carry_no_request_id_or_expiry_to_check():
    validate_command(B.sync(), state())
    validate_command(B.ready(1), state())


def test_accept_and_withdraw_are_checked_for_their_request_id_only():
    validate_command(B.accept("student-accept-1", "offer-1"), state())
    with pytest.raises(CommandRejected, match="request_id"):
        validate_command(B.withdraw("bad id", "ad-1"), state())
