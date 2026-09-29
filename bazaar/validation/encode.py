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


# --- the server's side -----------------------------------------------------
# Only the local sandbox (``bazaar/sandbox/``) sends these.  They live here, not
# there, so the generated bindings stay behind this one wall even for a server.

def _set_nullable(container, value) -> None:
    """Select exactly one arm: ``value``, or ``null: true`` for ``None``."""
    if value is None:
        container.null = True
    else:
        container.value = value


def _encode_result(out, result: model.Result) -> None:
    out.type = result.type.value
    out.protocol_version = result.protocol_version
    out.run_id = result.run_id
    out.request_id = result.request_id
    out.ok = result.ok
    out.code = result.code.value
    out.processed_tick = result.processed_tick
    out.processed_version = result.processed_version
    _set_nullable(out.object_id, result.object_id)
    _set_nullable(out.transaction_id, result.transaction_id)
    _set_nullable(out.retry_after_tick, result.retry_after_tick)


def _encode_protocol_error(out, error: model.ProtocolError) -> None:
    out.type = error.type.value
    out.protocol_version = error.protocol_version
    _set_nullable(out.run_id, error.run_id)
    _set_nullable(out.request_id, error.request_id)
    out.code = error.code.value
    out.close_session = error.close_session


def _encode_readiness(out, readiness: model.Readiness) -> None:
    out.type = readiness.type.value
    out.protocol_version = readiness.protocol_version
    out.run_id = readiness.run_id
    out.ready = readiness.ready
    out.snapshot_sequence = readiness.snapshot_sequence


def _encode_offer_record(out, offer: model.Offer) -> None:
    out.offer_id = offer.offer_id
    out.proposer_id = offer.proposer_id
    out.recipient_id = offer.recipient_id
    _set_bundle(out.give, offer.give)
    _set_bundle(out.receive, offer.receive)
    out.created_tick = offer.created_tick
    out.created_version = offer.created_version
    out.expires_tick = offer.expires_tick
    out.status = offer.status.value
    _set_nullable(out.closed_tick, offer.closed_tick)
    _set_nullable(out.transaction_id, offer.transaction_id)


def _encode_transaction(out, transaction: model.Transaction) -> None:
    out.transaction_id = transaction.transaction_id
    out.offer_id = transaction.offer_id
    out.proposer_id = transaction.proposer_id
    out.recipient_id = transaction.recipient_id
    _set_bundle(out.give, transaction.give)
    _set_bundle(out.receive, transaction.receive)
    out.settled_tick = transaction.settled_tick
    out.settled_version = transaction.settled_version


def _encode_advertisement(out, advertisement: model.Advertisement) -> None:
    out.advertisement_id = advertisement.advertisement_id
    out.station_id = advertisement.station_id
    _set_resources(out.selling, advertisement.selling)
    _set_resources(out.seeking, advertisement.seeking)
    out.created_tick = advertisement.created_tick
    out.expires_tick = advertisement.expires_tick
    out.created_version = advertisement.created_version
    out.status = advertisement.status.value


def _encode_observation(out, me: model.StationObservation) -> None:
    out.station_id = me.station_id
    _set_bundle(out.inventory, me.inventory)
    out.health = me.health
    out.failed_once = me.failed_once
    _set_nullable(out.first_failure_tick, me.first_failure_tick)
    _set_bundle(out.last_production, me.last_production)
    _set_bundle(out.last_unmet_upkeep, me.last_unmet_upkeep)
    out.fully_supplied_ticks = me.fully_supplied_ticks
    out.shortage_ticks = me.shortage_ticks
    out.current_shortage_streak = me.current_shortage_streak
    out.longest_shortage_streak = me.longest_shortage_streak
    _set_bundle(out.produced_total, me.produced_total)
    _set_bundle(out.consumed_total, me.consumed_total)
    _set_bundle(out.unmet_total, me.unmet_total)
    _set_bundle(out.imported_total, me.imported_total)
    _set_bundle(out.exported_total, me.exported_total)
    _set_bundle(out.upkeep_per_tick, me.upkeep_per_tick)
    out.specialty = me.specialty.value


def _encode_rules(out, rules: model.PublicRules) -> None:
    out.rules_version = rules.rules_version
    out.duration_ticks = rules.duration_ticks
    out.tick_duration_ms = rules.tick_duration_ms
    _set_resources(out.resource_order, rules.resource_order)
    out.max_health = rules.max_health
    out.shortage_damage_per_unit = rules.shortage_damage_per_unit
    out.recovery_per_fully_supplied_tick = rules.recovery_per_fully_supplied_tick
    out.max_publication_ttl_ticks = rules.max_publication_ttl_ticks
    out.max_offer_ttl_ticks = rules.max_offer_ttl_ticks
    out.new_commands_per_station_per_tick = rules.new_commands_per_station_per_tick
    out.max_request_records_per_station = rules.max_request_records_per_station
    out.max_open_outgoing_offers = rules.max_open_outgoing_offers
    out.max_command_bytes = rules.max_command_bytes


def _encode_items(container, items, encode_one) -> None:
    """Fill a list wrapper, keeping it present even when empty."""
    container.SetInParent()
    for item in items:
        encode_one(container.items.add(), item)


def _encode_state(out, state: model.State) -> None:
    out.type = state.type.value
    out.protocol_version = state.protocol_version
    out.run_id = state.run_id
    out.snapshot_sequence = state.snapshot_sequence
    out.world_version = state.world_version
    out.tick = state.tick
    out.phase = state.phase.value
    out.self_station_id = state.self_station_id
    _encode_rules(out.rules, state.rules)

    def directory_entry(target, entry: model.DirectoryEntry) -> None:
        target.station_id = entry.station_id
        target.display_name = entry.display_name

    _encode_items(out.directory, state.directory, directory_entry)
    _encode_observation(getattr(out, "self"), state.observation)
    _encode_items(out.offers, state.offers, _encode_offer_record)
    _encode_items(out.advertisements, state.advertisements, _encode_advertisement)
    _encode_items(out.transactions, state.transactions, _encode_transaction)
    _encode_items(out.request_results, state.request_results, _encode_result)
    if state.outcome is None:
        out.outcome.null = True
    else:
        value = out.outcome.value
        _set_nullable(value.collective_success, state.outcome.collective_success)
        value.self_failed = state.outcome.self_failed
        value.aborted = state.outcome.aborted


#: Each ServerMessage arm and the function that fills it.
_SERVER_ENCODERS = {
    "state": _encode_state,
    "result": _encode_result,
    "protocol_error": _encode_protocol_error,
    "readiness": _encode_readiness,
}


def encode_server_message(message: model.ServerMessage):
    """One ``model.ServerMessage`` → one ``pb.ServerMessage``."""
    arm = message.which()
    out = pb.ServerMessage()
    _SERVER_ENCODERS[arm](getattr(out, arm), getattr(message, arm))
    return out


def encode_server_bytes(message: model.ServerMessage) -> bytes:
    """``model.ServerMessage`` → the binary frame a server puts on the socket."""
    return encode_server_message(message).SerializeToString()
