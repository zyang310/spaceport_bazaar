"""Wire → domain.  Turns ``bazaar_pb2`` messages into ``model`` types.

This file and ``encode.py`` are the only two allowed to import the generated
bindings; see ``docs/architecture.md``.  Everything here is mechanical: unwrap
the list containers, flatten the two-armed nullables, and name the enums.  No
validation and no judgement -- ``limits.py`` and ``brain/`` do that.
"""

from ..generated import bazaar_pb2 as pb
from . import model


def unwrap(nullable):
    """Flatten a proto nullable wrapper to ``value`` or ``None``.

    The schema is explicit that a wrapper must select exactly one arm and that
    ``null`` must be true, so an unset wrapper is a malformed message rather
    than an absent value, and we refuse to guess which was meant.
    """
    arm = nullable.WhichOneof("kind")
    if arm is None:
        raise ValueError("nullable wrapper has neither arm set")
    if arm == "null":
        if not nullable.null:
            raise ValueError("nullable wrapper set null: false, which is rejected")
        return None
    return nullable.value


def _resources(container) -> list[model.Resource]:
    """``ListResource`` → a plain list, dropping the wrapper."""
    return [model.Resource(item) for item in container.items]


def _bundle(pb_bundle) -> model.Bundle:
    return model.Bundle(
        water=pb_bundle.water,
        food=pb_bundle.food,
        components=pb_bundle.components,
    )


def decode_result(r) -> model.Result:
    return model.Result(
        type=model.ResultType(r.type),
        protocol_version=r.protocol_version,
        run_id=r.run_id,
        request_id=r.request_id,
        ok=r.ok,
        code=model.ResultCode(r.code),
        processed_tick=r.processed_tick,
        processed_version=r.processed_version,
        object_id=unwrap(r.object_id),
        transaction_id=unwrap(r.transaction_id),
        retry_after_tick=unwrap(r.retry_after_tick),
    )


def decode_protocol_error(e) -> model.ProtocolError:
    return model.ProtocolError(
        type=model.ProtocolErrorType(e.type),
        protocol_version=e.protocol_version,
        run_id=unwrap(e.run_id),
        request_id=unwrap(e.request_id),
        code=model.ControlCode(e.code),
        close_session=e.close_session,
    )


def decode_readiness(r) -> model.Readiness:
    return model.Readiness(
        type=model.ReadinessType(r.type),
        protocol_version=r.protocol_version,
        run_id=r.run_id,
        ready=r.ready,
        snapshot_sequence=r.snapshot_sequence,
    )


def decode_offer(o) -> model.Offer:
    return model.Offer(
        offer_id=o.offer_id,
        proposer_id=o.proposer_id,
        recipient_id=o.recipient_id,
        give=_bundle(o.give),
        receive=_bundle(o.receive),
        created_tick=o.created_tick,
        created_version=o.created_version,
        expires_tick=o.expires_tick,
        status=model.OfferStatus(o.status),
        closed_tick=unwrap(o.closed_tick),
        transaction_id=unwrap(o.transaction_id),
    )


def decode_transaction(t) -> model.Transaction:
    return model.Transaction(
        transaction_id=t.transaction_id,
        offer_id=t.offer_id,
        proposer_id=t.proposer_id,
        recipient_id=t.recipient_id,
        give=_bundle(t.give),
        receive=_bundle(t.receive),
        settled_tick=t.settled_tick,
        settled_version=t.settled_version,
    )


def decode_advertisement(a) -> model.Advertisement:
    return model.Advertisement(
        advertisement_id=a.advertisement_id,
        station_id=a.station_id,
        selling=_resources(a.selling),
        seeking=_resources(a.seeking),
        created_tick=a.created_tick,
        expires_tick=a.expires_tick,
        created_version=a.created_version,
        status=model.PublicationStatus(a.status),
    )


def decode_station_observation(s) -> model.StationObservation:
    return model.StationObservation(
        station_id=s.station_id,
        inventory=_bundle(s.inventory),
        health=s.health,
        failed_once=s.failed_once,
        first_failure_tick=unwrap(s.first_failure_tick),
        last_production=_bundle(s.last_production),
        last_unmet_upkeep=_bundle(s.last_unmet_upkeep),
        fully_supplied_ticks=s.fully_supplied_ticks,
        shortage_ticks=s.shortage_ticks,
        current_shortage_streak=s.current_shortage_streak,
        longest_shortage_streak=s.longest_shortage_streak,
        produced_total=_bundle(s.produced_total),
        consumed_total=_bundle(s.consumed_total),
        unmet_total=_bundle(s.unmet_total),
        imported_total=_bundle(s.imported_total),
        exported_total=_bundle(s.exported_total),
        upkeep_per_tick=_bundle(s.upkeep_per_tick),
        specialty=model.Resource(s.specialty),
    )


def decode_rules(r) -> model.PublicRules:
    return model.PublicRules(
        rules_version=r.rules_version,
        duration_ticks=r.duration_ticks,
        tick_duration_ms=r.tick_duration_ms,
        resource_order=_resources(r.resource_order),
        max_health=r.max_health,
        shortage_damage_per_unit=r.shortage_damage_per_unit,
        recovery_per_fully_supplied_tick=r.recovery_per_fully_supplied_tick,
        max_publication_ttl_ticks=r.max_publication_ttl_ticks,
        max_offer_ttl_ticks=r.max_offer_ttl_ticks,
        new_commands_per_station_per_tick=r.new_commands_per_station_per_tick,
        max_request_records_per_station=r.max_request_records_per_station,
        max_open_outgoing_offers=r.max_open_outgoing_offers,
        max_command_bytes=r.max_command_bytes,
    )


def _outcome(nullable) -> model.PlayerOutcome | None:
    """``NullablePlayerOutcome`` needs its value arm decoded, not just read."""
    arm = nullable.WhichOneof("kind")
    if arm is None:
        raise ValueError("outcome wrapper has neither arm set")
    if arm == "null":
        if not nullable.null:
            raise ValueError("outcome wrapper set null: false, which is rejected")
        return None
    value = nullable.value
    return model.PlayerOutcome(
        collective_success=unwrap(value.collective_success),
        self_failed=value.self_failed,
        aborted=value.aborted,
    )


def decode_state(s) -> model.State:
    return model.State(
        type=model.StateType(s.type),
        protocol_version=s.protocol_version,
        run_id=s.run_id,
        snapshot_sequence=s.snapshot_sequence,
        world_version=s.world_version,
        tick=s.tick,
        phase=model.Phase(s.phase),
        self_station_id=s.self_station_id,
        rules=decode_rules(s.rules),
        directory=[
            model.DirectoryEntry(station_id=d.station_id, display_name=d.display_name)
            for d in s.directory.items
        ],
        observation=decode_station_observation(getattr(s, "self")),
        offers=[decode_offer(o) for o in s.offers.items],
        advertisements=[decode_advertisement(a) for a in s.advertisements.items],
        transactions=[decode_transaction(t) for t in s.transactions.items],
        request_results=[decode_result(r) for r in s.request_results.items],
        outcome=_outcome(s.outcome),
    )


#: Each ServerMessage arm and the function that decodes it.
_SERVER_DECODERS = {
    "state": decode_state,
    "result": decode_result,
    "protocol_error": decode_protocol_error,
    "readiness": decode_readiness,
}


def decode_server_message(msg) -> model.ServerMessage:
    """One ``pb.ServerMessage`` → one ``model.ServerMessage``."""
    arm = msg.WhichOneof("message")
    if arm is None:
        raise ValueError("ServerMessage has no arm set")
    return model.ServerMessage(**{arm: _SERVER_DECODERS[arm](getattr(msg, arm))})


def decode_bytes(raw: bytes) -> model.ServerMessage:
    """Raw binary frame → ``model.ServerMessage``."""
    return decode_server_message(pb.ServerMessage.FromString(raw))
