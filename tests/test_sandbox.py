"""The local sandbox game: its codec, its rules, its socket, and the full loop.

The rules tests drive ``World`` directly, one tick at a time, on a three-station
world.  The socket tests bind port 0 and use plain ``asyncio.run``, as
``test_runner.py`` does.  The last test runs the real client, a real policy and
the dashboard against the sandbox, which is what the sandbox is for.
"""

import asyncio
import random
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from bazaar import config
from bazaar.brain.policy import HustlerPolicy
from bazaar.dashboard import Dashboard
from bazaar.network.transport import BazaarClient, ProtocolErrorReceived
from bazaar.runlog import RunLog
from bazaar.runner import RunOutcome, fan_out, run_utility
from bazaar.sandbox import SandboxServer, World
from bazaar.sandbox.bots import bots_act
from bazaar.validation import decode, encode, model
from bazaar.validation.commands import CommandBuilder

from .factories import COMPONENTS, FOOD, WATER

FIXTURES = Path(__file__).parent / "fixtures"
C = model.ResultCode
SMALL = replace(
    config.SANDBOX, stations=3, station="P01", duration_ticks=10, tick_duration_ms=100, start_after_seconds=0
)


def running_world(**overrides) -> World:
    world = World(replace(SMALL, **overrides))
    world.start()
    return world


def send(world: World, station: str, message: model.ClientMessage):
    return world.command(station, message.inner())


def inventory(world: World, station: str) -> tuple[int, int, int]:
    return tuple(world.stations[station].inventory.values())


# --- the codec additions -----------------------------------------------------------

def test_every_captured_server_frame_re_encodes_byte_for_byte():
    for frame in sorted(FIXTURES.glob("*.bin")):
        raw = frame.read_bytes()
        assert encode.encode_server_bytes(decode.decode_bytes(raw)) == raw, frame.name


def test_every_client_command_survives_encode_then_decode():
    b = CommandBuilder("run-1")
    for message in [
        b.advertise("r1", [WATER], [], 6),
        b.offer("r2", "P02", model.Bundle(water=2), model.Bundle(food=1), 6),
        b.accept("r3", "offer-1"),
        b.withdraw("r4", "advertisement-2"),
        b.sync(),
        b.ready(1),
    ]:
        assert decode.decode_client_bytes(encode.encode_bytes(message)) == message


def test_a_client_frame_missing_required_fields_is_refused():
    with pytest.raises(ValueError, match="missing required fields"):
        decode.decode_client_bytes(b"\x0a\x00")  # an advertise with nothing in it


# --- the rules -----------------------------------------------------------------------

def test_a_tick_makes_the_specialty_and_eats_one_of_everything():
    world = running_world()
    world.advance()
    # P01 makes water: +6, then everything -1.
    assert inventory(world, "P01") == (35, 29, 29)
    assert world.state_for("P01", 1).observation.last_production == model.Bundle(6, 0, 0)


def test_each_unit_short_costs_health_and_zero_health_is_for_good():
    world = running_world(start_inventory=0, max_health=20, duration_ticks=50)
    world.advance()  # P01 makes water; short one food and one component
    p01 = world.stations["P01"]
    assert (p01.health, p01.current_shortage_streak) == (10, 1)
    world.advance()
    assert p01.health == 0 and p01.failed and p01.first_failure_tick == 2
    listing = CommandBuilder(world.run_id).advertise("a", [WATER], [], world.tick + 2)
    assert send(world, "P01", listing).code is C.RESULT_CODE_STATION_FAILED


def test_proposing_reserves_nothing_and_accepting_moves_both_sides():
    world = running_world()
    b = CommandBuilder(world.run_id)
    offered = send(world, "P01", b.offer("o1", "P02", model.Bundle(water=2), model.Bundle(food=1), 5))
    assert offered.ok and offered.object_id.startswith("offer-")
    assert inventory(world, "P01") == (30, 30, 30)

    accepted = send(world, "P02", b.accept("a1", offered.object_id))
    assert accepted.ok and accepted.transaction_id.startswith("transaction-")
    assert inventory(world, "P01") == (28, 31, 30)
    assert inventory(world, "P02") == (32, 29, 30)
    seen = world.state_for("P01", 1)
    assert seen.observation.exported_total == model.Bundle(2, 0, 0)
    assert seen.observation.imported_total == model.Bundle(0, 1, 0)
    assert seen.offers[0].status is model.OfferStatus.OFFER_STATUS_ACCEPTED
    assert len(seen.transactions) == 1


def test_an_offer_the_proposer_can_no_longer_pay_for_is_insufficient_resources():
    world = running_world()
    b = CommandBuilder(world.run_id)
    offered = send(world, "P01", b.offer("o1", "P02", model.Bundle(water=31), model.Bundle(), 5))
    assert send(world, "P02", b.accept("a1", offered.object_id)).code is C.RESULT_CODE_INSUFFICIENT_RESOURCES


def test_only_the_recipient_can_accept_and_only_while_open():
    world = running_world()
    b = CommandBuilder(world.run_id)
    offer_id = send(world, "P01", b.offer("o1", "P02", model.Bundle(water=1), model.Bundle(), 2)).object_id
    assert send(world, "P03", b.accept("a1", offer_id)).code is C.RESULT_CODE_NOT_FOUND
    world.advance()
    world.advance()  # tick 2 is its expiry tick
    assert world.offers[offer_id].status is model.OfferStatus.OFFER_STATUS_EXPIRED
    assert send(world, "P02", b.accept("a2", offer_id)).code is C.RESULT_CODE_NOT_OPEN


def test_an_exact_retry_returns_the_stored_result_without_acting_twice():
    world = running_world()
    message = CommandBuilder(world.run_id).offer("o1", "P02", model.Bundle(water=1), model.Bundle(), 5)
    first = send(world, "P01", message)
    version = world.version
    assert send(world, "P01", message) == first
    assert world.version == version
    assert len(world.offers) == 1


def test_reusing_a_request_id_with_a_different_body_is_a_conflict():
    world = running_world()
    b = CommandBuilder(world.run_id)
    send(world, "P01", b.advertise("same-id", [WATER], [], 5))
    version = world.version
    conflict = send(world, "P01", b.advertise("same-id", [WATER], [FOOD], 5))
    assert conflict.code is C.RESULT_CODE_REQUEST_ID_CONFLICT
    assert world.version == version  # a conflict is not a revision


def test_the_command_past_the_per_tick_limit_is_rate_limited_until_the_next_tick():
    world = running_world(new_commands_per_station_per_tick=2)
    b = CommandBuilder(world.run_id)
    codes = [send(world, "P01", b.advertise(f"a{n}", [WATER], [], 5)).code for n in range(3)]
    assert codes == [C.RESULT_CODE_OK, C.RESULT_CODE_OK, C.RESULT_CODE_RATE_LIMITED]
    assert world.stations["P01"].records["a2"][1].retry_after_tick == 1
    world.advance()
    assert send(world, "P01", b.advertise("a3", [WATER], [], 5)).ok


def test_the_request_record_cap_is_a_protocol_error_not_a_result():
    world = running_world(max_request_records_per_station=1)
    b = CommandBuilder(world.run_id)
    send(world, "P01", b.advertise("a1", [WATER], [], 5))
    answer = send(world, "P01", b.advertise("a2", [WATER], [], 5))
    assert isinstance(answer, model.ProtocolError)
    assert answer.code is model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
    assert not answer.close_session and answer.request_id == "a2"


def test_commands_before_the_start_are_run_not_running():
    world = World(SMALL)
    answer = send(world, "P01", CommandBuilder(world.run_id).advertise("a1", [WATER], [], 5))
    assert answer.code is C.RESULT_CODE_RUN_NOT_RUNNING


def test_a_new_listing_replaces_the_old_and_everyone_sees_active_listings():
    world = running_world()
    b = CommandBuilder(world.run_id)
    send(world, "P01", b.advertise("a1", [WATER], [FOOD], 5))
    send(world, "P01", b.advertise("a2", [], [COMPONENTS], 5))
    send(world, "P02", b.advertise("a3", [FOOD], [], 5))
    listings = world.state_for("P03", 1).advertisements
    assert sorted((a.station_id, tuple(a.seeking)) for a in listings) == [("P01", (COMPONENTS,)), ("P02", ())]


def test_a_state_shows_only_the_offers_we_are_party_to():
    world = running_world()
    b = CommandBuilder(world.run_id)
    send(world, "P01", b.offer("o1", "P02", model.Bundle(water=1), model.Bundle(), 5))
    send(world, "P02", b.offer("o2", "P03", model.Bundle(food=1), model.Bundle(), 5))
    assert [o.recipient_id for o in world.state_for("P01", 1).offers] == ["P02"]
    assert len(world.state_for("P02", 1).offers) == 2


def test_the_run_ends_with_open_offers_marked_run_ended_and_an_outcome():
    world = running_world(duration_ticks=2)
    offer_id = send(world, "P01", CommandBuilder(world.run_id).offer(
        "o1", "P02", model.Bundle(water=1), model.Bundle(), 5)).object_id
    world.advance()
    assert world.state_for("P01", 1).outcome is None
    world.advance()
    final = world.state_for("P01", 1)
    assert final.phase is model.Phase.PHASE_FINISHED
    assert world.offers[offer_id].status is model.OfferStatus.OFFER_STATUS_RUN_ENDED
    assert final.outcome == model.PlayerOutcome(collective_success=True, self_failed=False, aborted=False)


def test_the_same_seed_replays_the_same_market():
    def run():
        world, rng = running_world(duration_ticks=30, stations=6), random.Random(3)
        for _ in range(30):
            bots_act(world, rng)
            world.advance()
        return world.state_for("P01", 1)

    first = run()
    assert first == run()
    assert first.transactions  # the bots actually traded


# --- the socket ------------------------------------------------------------------------

def with_server(scenario, **overrides):
    """Run ``scenario(server)`` against a sandbox on a free port."""

    async def wrapper():
        server = SandboxServer(World(replace(SMALL, **overrides)), start_after=0)
        await server.start("127.0.0.1", 0)
        try:
            return await scenario(server)
        finally:
            await server.stop()

    return asyncio.run(wrapper())


def test_the_handshake_wants_a_token_and_the_subprotocol():
    async def scenario(server):
        codes = []
        for headers, subprotocols in [({}, ["bazaar.protobuf.v2"]), ({"Authorization": "Bearer x"}, None)]:
            try:
                async with connect(server.url, additional_headers=headers, subprotocols=subprotocols):
                    pass
            except InvalidStatus as exc:
                codes.append(exc.response.status_code)
        return codes

    assert with_server(scenario) == [401, 400]


def test_a_token_can_name_the_station_it_plays():
    async def scenario(server):
        async with BazaarClient(server.url, "sandbox-P02") as client:
            return (await client.first_state()).self_station_id

    assert with_server(scenario, station="P03") == "P02"


def test_a_default_station_that_does_not_exist_is_refused_up_front():
    with pytest.raises(ValueError, match="P08"):
        SandboxServer(World(replace(SMALL, station="P08")))


def test_trading_before_readiness_is_a_bad_message_that_keeps_the_session():
    async def scenario(server):
        async with BazaarClient(server.url, "anything") as client:
            state = await client.first_state()
            message = CommandBuilder(state.run_id).advertise("early", [WATER], [], 5)
            with pytest.raises(ProtocolErrorReceived) as caught:
                await client.request(message, "early", timeout=2)
            await client.declare_ready(state.snapshot_sequence)
            return caught.value.error, await client.sync()

    error, state_after = with_server(scenario)
    assert error.code is model.ControlCode.CONTROL_CODE_BAD_MESSAGE and not error.close_session
    assert state_after.snapshot_sequence >= 2


def test_the_real_client_trades_against_the_sandbox_and_the_dashboard_sees_it(tmp_path):
    async def scenario(server):
        dashboard = Dashboard()
        async with BazaarClient(server.url, "sandbox", on_state=fan_out(dashboard.on_state)) as client:
            outcome = await run_utility(
                client, HustlerPolicy(), RunLog(tmp_path), RunOutcome(), 0.6, activity=dashboard, session="s1"
            )
        return outcome, dashboard

    outcome, dashboard = with_server(scenario, duration_ticks=8)
    assert not outcome.protocol_errors
    assert dashboard.state.tick > 0
    assert dashboard.totals["ok"] > 0
    assert dashboard.errors == 0
