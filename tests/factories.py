"""Builders for synthetic ``model`` objects used across the tests.

The policy is a pure function of the state it is handed, which is what makes it
testable offline -- but only if a state is cheap to construct.  These helpers
fill every required field with a sensible default so a test can name just the
one or two fields it actually cares about.

Defaults mirror the practice exercise's opening position: station P01, water
specialty, inventory (30,30,30), tick 0, PHASE_RUNNING.
"""

from bazaar.validation import model

R = model.Resource
WATER, FOOD, COMPONENTS = R.RESOURCE_WATER, R.RESOURCE_FOOD, R.RESOURCE_COMPONENTS


def bundle(water=0, food=0, components=0) -> model.Bundle:
    return model.Bundle(water=water, food=food, components=components)


def rules(**overrides) -> model.PublicRules:
    defaults = dict(
        rules_version="test",
        duration_ticks=100,
        tick_duration_ms=1000,
        resource_order=[WATER, FOOD, COMPONENTS],
        max_health=100,
        shortage_damage_per_unit=1,
        recovery_per_fully_supplied_tick=1,
        max_publication_ttl_ticks=10,
        max_offer_ttl_ticks=10,
        new_commands_per_station_per_tick=3,
        max_request_records_per_station=5,
        max_open_outgoing_offers=2,
        max_command_bytes=16384,
    )
    return model.PublicRules(**{**defaults, **overrides})


def observation(**overrides) -> model.StationObservation:
    defaults = dict(
        station_id="P01",
        inventory=bundle(30, 30, 30),
        health=100,
        failed_once=False,
        first_failure_tick=None,
        last_production=bundle(),
        last_unmet_upkeep=bundle(),
        fully_supplied_ticks=0,
        shortage_ticks=0,
        current_shortage_streak=0,
        longest_shortage_streak=0,
        produced_total=bundle(),
        consumed_total=bundle(),
        unmet_total=bundle(),
        imported_total=bundle(),
        exported_total=bundle(),
        upkeep_per_tick=bundle(1, 1, 0),
        specialty=WATER,
    )
    return model.StationObservation(**{**defaults, **overrides})


def offer(
    offer_id="offer-1",
    proposer_id="P02",
    recipient_id="P01",
    give=(0, 0, 1),
    receive=(0, 0, 0),
    status=model.OfferStatus.OFFER_STATUS_OPEN,
    expires_tick=6,
    **overrides,
) -> model.Offer:
    defaults = dict(
        offer_id=offer_id,
        proposer_id=proposer_id,
        recipient_id=recipient_id,
        give=model.Bundle.of(give),
        receive=model.Bundle.of(receive),
        created_tick=0,
        created_version=1,
        expires_tick=expires_tick,
        status=status,
        closed_tick=None,
        transaction_id=None,
    )
    return model.Offer(**{**defaults, **overrides})


def advertisement(
    advertisement_id="ad-1",
    station_id="P02",
    selling=(FOOD,),
    seeking=(WATER,),
    status=model.PublicationStatus.PUBLICATION_STATUS_ACTIVE,
    **overrides,
) -> model.Advertisement:
    defaults = dict(
        advertisement_id=advertisement_id,
        station_id=station_id,
        selling=list(selling),
        seeking=list(seeking),
        created_tick=0,
        expires_tick=6,
        created_version=1,
        status=status,
    )
    return model.Advertisement(**{**defaults, **overrides})


def result(request_id="student-advertise-1", **overrides) -> model.Result:
    defaults = dict(
        type=model.ResultType.RESULT_TYPE_RESULT,
        protocol_version="2.0",
        run_id="run-1",
        request_id=request_id,
        ok=True,
        code=model.ResultCode.RESULT_CODE_OK,
        processed_tick=0,
        processed_version=3,
        object_id=None,
        transaction_id=None,
        retry_after_tick=None,
    )
    return model.Result(**{**defaults, **overrides})


def state(**overrides) -> model.State:
    """A full ``State``; pass only the fields the test cares about."""
    defaults = dict(
        type=model.StateType.STATE_TYPE_STATE,
        protocol_version="2.0",
        run_id="run-1",
        snapshot_sequence=1,
        world_version=2,
        tick=0,
        phase=model.Phase.PHASE_RUNNING,
        self_station_id="P01",
        rules=rules(),
        directory=[
            model.DirectoryEntry(station_id="P01", display_name="P01"),
            model.DirectoryEntry(station_id="P02", display_name="P02"),
        ],
        observation=observation(),
        offers=[],
        advertisements=[],
        transactions=[],
        request_results=[],
        outcome=None,
    )
    return model.State(**{**defaults, **overrides})
