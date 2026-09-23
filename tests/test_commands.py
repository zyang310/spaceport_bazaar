"""``CommandBuilder`` sets the right arm, with the run ID threaded through.

The builder validates nothing on purpose -- legality is ``limits.py``'s job --
so these tests only check assembly.
"""

from bazaar.validation import model
from bazaar.validation.commands import PROTOCOL_VERSION, CommandBuilder

from .factories import COMPONENTS, FOOD, WATER

RUN = "run-1"


def builder() -> CommandBuilder:
    return CommandBuilder(RUN)


def test_advertise_sets_its_arm_body_and_type():
    message = builder().advertise("student-advertise-1", [WATER], [FOOD], 6)
    assert message.which() == "advertise"
    command = message.advertise
    assert command.type is model.AdvertiseType.ADVERTISE_TYPE_ADVERTISE
    assert (command.run_id, command.request_id) == (RUN, "student-advertise-1")
    assert command.protocol_version == PROTOCOL_VERSION
    assert (command.body.selling, command.body.seeking) == ([WATER], [FOOD])
    assert command.body.expires_tick == 6


def test_advertise_accepts_an_empty_selling_list():
    """Guide step 3 advertises exactly this: sell nothing, seek components."""
    body = builder().advertise("student-advertise-seeking-1", [], [COMPONENTS], 6).advertise.body
    assert body.selling == []
    assert body.seeking == [COMPONENTS]


def test_offer_keeps_the_proposers_perspective():
    give, receive = model.Bundle(water=2), model.Bundle(food=1)
    command = builder().offer("student-offer-1", "P02", give, receive, 6).offer
    assert command.type is model.OfferCommandType.OFFER_COMMAND_TYPE_OFFER
    assert command.body.recipient_id == "P02"
    assert command.body.give is give and command.body.receive is receive


def test_accept_and_withdraw_carry_only_an_id():
    accept = builder().accept("student-accept-1", "offer-7").accept
    assert accept.body.offer_id == "offer-7"
    withdraw = builder().withdraw("student-withdraw-1", "ad-3").withdraw
    assert withdraw.body.object_id == "ad-3"


def test_ready_echoes_the_snapshot_sequence_and_has_no_request_id():
    ready = builder().ready(snapshot_sequence=1).ready
    assert ready.ready is True and ready.snapshot_sequence == 1
    assert not hasattr(ready, "request_id")


def test_ready_false_is_expressible():
    assert builder().ready(2, ready=False).ready.ready is False


def test_sync_has_neither_request_id_nor_body():
    sync = builder().sync().sync
    assert sync.run_id == RUN
    assert not hasattr(sync, "request_id") and not hasattr(sync, "body")
