"""The domain model's own guarantees: bundle arithmetic and one-arm envelopes."""

import pytest

from bazaar.validation import model

from .factories import COMPONENTS, FOOD, WATER


def test_bundle_round_trips_through_a_tuple():
    b = model.Bundle.of((2, 0, 1))
    assert b.as_tuple() == (2, 0, 1)
    assert model.Bundle.of(b.as_tuple()) == b


def test_bundle_reads_a_resource_by_enum():
    b = model.Bundle(water=5, food=7, components=9)
    assert (b.get(WATER), b.get(FOOD), b.get(COMPONENTS)) == (5, 7, 9)


def test_bundle_defaults_to_zeros_and_knows_it():
    assert model.Bundle().as_tuple() == (0, 0, 0)
    assert model.Bundle().is_zero()
    assert not model.Bundle(components=1).is_zero()


def test_client_message_reports_its_single_arm():
    sync = model.Sync(
        type=model.SyncType.SYNC_TYPE_SYNC, protocol_version="2.0", run_id="run-1"
    )
    message = model.ClientMessage(sync=sync)
    assert message.which() == "sync"
    assert message.inner() is sync


def test_client_message_rejects_zero_or_several_arms():
    sync = model.Sync(
        type=model.SyncType.SYNC_TYPE_SYNC, protocol_version="2.0", run_id="run-1"
    )
    ready = model.Ready(
        type=model.ReadyType.READY_TYPE_READY,
        protocol_version="2.0",
        run_id="run-1",
        ready=True,
        snapshot_sequence=1,
    )
    with pytest.raises(ValueError):
        model.ClientMessage().which()
    with pytest.raises(ValueError):
        model.ClientMessage(sync=sync, ready=ready).which()


def test_server_message_reports_its_single_arm():
    readiness = model.Readiness(
        type=model.ReadinessType.READINESS_TYPE_READINESS,
        protocol_version="2.0",
        run_id="run-1",
        ready=True,
        snapshot_sequence=1,
    )
    assert model.ServerMessage(readiness=readiness).which() == "readiness"
    with pytest.raises(ValueError):
        model.ServerMessage().which()


def test_states_are_frozen_because_a_snapshot_is_a_reported_fact():
    from .factories import state

    with pytest.raises(Exception):
        state().tick = 5
