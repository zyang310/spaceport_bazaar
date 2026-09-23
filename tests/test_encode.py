"""The encode/decode pair: presence, zeros, enums, and nullable arms.

Tests may import the generated bindings even though ``bazaar/`` outside
``encode.py``/``decode.py`` may not -- checking the wire format is the whole
point of this file.
"""

import pytest

from bazaar.generated import bazaar_pb2 as pb
from bazaar.validation import decode, encode, model
from bazaar.validation.commands import CommandBuilder

from .factories import COMPONENTS, FOOD, WATER

RUN = "run-1"


def reparse(message: model.ClientMessage):
    """Encode, serialize, and read back as a fresh protobuf message."""
    return pb.ClientMessage.FromString(encode.encode_bytes(message))


# --- outbound: model -> wire ----------------------------------------------

def test_every_arm_serializes_and_keeps_its_arm():
    b = CommandBuilder(RUN)
    cases = {
        "advertise": b.advertise("r1", [WATER], [FOOD], 6),
        "offer": b.offer("r2", "P02", model.Bundle(water=2), model.Bundle(food=1), 6),
        "accept": b.accept("r3", "offer-1"),
        "withdraw": b.withdraw("r4", "ad-1"),
        "ready": b.ready(1),
        "sync": b.sync(),
    }
    for arm, message in cases.items():
        assert reparse(message).WhichOneof("message") == arm


def test_an_empty_selling_list_stays_present_on_the_wire():
    """``selling {}`` means "I sell nothing"; omitting it is a bad message."""
    message = CommandBuilder(RUN).advertise("r1", [], [COMPONENTS], 6)
    body = reparse(message).advertise.body
    assert body.HasField("selling")
    assert list(body.selling.items) == []
    assert list(body.seeking.items) == [pb.RESOURCE_COMPONENTS]


def test_bundle_zeros_are_written_rather_than_omitted():
    """A zero-price gift is all zeros on one side, and must still encode."""
    message = CommandBuilder(RUN).offer(
        "r1", "P02", model.Bundle(components=1), model.Bundle(), 6
    )
    receive = reparse(message).offer.body.receive
    assert (receive.HasField("water"), receive.HasField("food"), receive.HasField("components")) == (
        True,
        True,
        True,
    )
    assert (receive.water, receive.food, receive.components) == (0, 0, 0)


def test_resource_enums_encode_with_their_proto_values():
    body = reparse(CommandBuilder(RUN).advertise("r1", [WATER, COMPONENTS], [FOOD], 6)).advertise.body
    assert list(body.selling.items) == [pb.RESOURCE_WATER, pb.RESOURCE_COMPONENTS]


def test_a_missing_required_field_fails_at_encode_time():
    broken = model.ClientMessage(
        accept=model.Accept(
            type=model.AcceptType.ACCEPT_TYPE_ACCEPT,
            protocol_version="2.0",
            run_id=RUN,
            request_id="r1",
            body=None,
        )
    )
    with pytest.raises(Exception):
        encode.encode_bytes(broken)


def test_encoding_an_envelope_with_no_arm_is_refused():
    with pytest.raises(ValueError):
        encode.encode_bytes(model.ClientMessage())


# --- inbound: wire -> model ------------------------------------------------

def test_unwrap_handles_both_arms():
    present = pb.NullableString()
    present.value = "ad-7"
    assert decode.unwrap(present) == "ad-7"

    absent = pb.NullableString()
    absent.null = True
    assert decode.unwrap(absent) is None


def test_unwrap_raises_when_neither_arm_is_set():
    with pytest.raises(ValueError):
        decode.unwrap(pb.NullableString())


def test_unwrap_rejects_null_false():
    """The schema rejects ``null: false``, so we refuse to guess what it meant."""
    wrapper = pb.NullableString()
    wrapper.null = False
    with pytest.raises(ValueError):
        decode.unwrap(wrapper)


def test_decode_result_names_its_enums_and_flattens_nullables():
    raw = pb.Result(
        type=pb.RESULT_TYPE_RESULT,
        protocol_version="2.0",
        run_id=RUN,
        request_id="student-advertise-1",
        ok=True,
        code=pb.RESULT_CODE_OK,
        processed_tick=0,
        processed_version=3,
        object_id=pb.NullableString(value="ad-1"),
        transaction_id=pb.NullableString(null=True),
        retry_after_tick=pb.NullableUint(null=True),
    )
    result = decode.decode_result(raw)
    assert result.code is model.ResultCode.RESULT_CODE_OK
    assert result.object_id == "ad-1"
    assert result.transaction_id is None and result.retry_after_tick is None


def test_decode_protocol_error_keeps_the_request_id_and_close_flag():
    raw = pb.ProtocolError(
        type=pb.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR,
        protocol_version="2.0",
        run_id=pb.NullableString(value=RUN),
        request_id=pb.NullableString(value="student-advertise-2"),
        code=pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
        close_session=False,
    )
    error = decode.decode_protocol_error(raw)
    assert error.code is model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
    assert error.request_id == "student-advertise-2"
    assert error.close_session is False


def test_decode_server_message_refuses_an_empty_envelope():
    with pytest.raises(ValueError):
        decode.decode_server_message(pb.ServerMessage())
