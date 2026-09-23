"""Domain → wire.  Turns ``model`` types into ``bazaar_pb2`` messages.

The mirror of ``decode.py``, and the other file allowed to import the generated
bindings.  Two details of proto2 presence drive most of this code:

* **Empty lists must still be present.**  ``selling {}`` means "I sell
  nothing", while omitting ``selling`` is a malformed message.  Extending a
  repeated field with nothing does not mark its container as present, so every
  list container gets an explicit ``SetInParent()``.
* **Zeros must be written.**  A ``Bundle`` field left unassigned is absent, not
  zero, so all three are always assigned.
"""

from ..generated import bazaar_pb2 as pb
from . import model


def _set_resources(container, resources) -> None:
    """Fill a ``ListResource``, keeping it present even when empty."""
    container.SetInParent()
    container.items.extend(resource.value for resource in resources)


def _set_bundle(target, bundle: model.Bundle) -> None:
    """Assign all three quantities, so zeros are written rather than omitted."""
    target.water = bundle.water
    target.food = bundle.food
    target.components = bundle.components


def _encode_advertise(msg, command: model.Advertise) -> None:
    out = msg.advertise
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id
    out.request_id = command.request_id
    _set_resources(out.body.selling, command.body.selling)
    _set_resources(out.body.seeking, command.body.seeking)
    out.body.expires_tick = command.body.expires_tick


def _encode_offer(msg, command: model.OfferCommand) -> None:
    out = msg.offer
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id
    out.request_id = command.request_id
    out.body.recipient_id = command.body.recipient_id
    _set_bundle(out.body.give, command.body.give)
    _set_bundle(out.body.receive, command.body.receive)
    out.body.expires_tick = command.body.expires_tick


def _encode_accept(msg, command: model.Accept) -> None:
    out = msg.accept
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id
    out.request_id = command.request_id
    out.body.offer_id = command.body.offer_id


def _encode_withdraw(msg, command: model.Withdraw) -> None:
    out = msg.withdraw
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id
    out.request_id = command.request_id
    out.body.object_id = command.body.object_id


def _encode_sync(msg, command: model.Sync) -> None:
    out = msg.sync
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id


def _encode_ready(msg, command: model.Ready) -> None:
    out = msg.ready
    out.type = command.type.value
    out.protocol_version = command.protocol_version
    out.run_id = command.run_id
    out.ready = command.ready
    out.snapshot_sequence = command.snapshot_sequence


#: Each ClientMessage arm and the function that fills it.
_CLIENT_ENCODERS = {
    "advertise": _encode_advertise,
    "offer": _encode_offer,
    "accept": _encode_accept,
    "withdraw": _encode_withdraw,
    "sync": _encode_sync,
    "ready": _encode_ready,
}


def encode_client_message(message: model.ClientMessage):
    """One ``model.ClientMessage`` → one ``pb.ClientMessage``."""
    arm = message.which()
    out = pb.ClientMessage()
    _CLIENT_ENCODERS[arm](out, getattr(message, arm))
    return out


def encode_bytes(message: model.ClientMessage) -> bytes:
    """``model.ClientMessage`` → the binary frame to put on the socket.

    Raises ``EncodeError`` if any required field is missing, which is the
    cheapest place to catch a malformed command: before it reaches the server.
    """
    return encode_client_message(message).SerializeToString()
