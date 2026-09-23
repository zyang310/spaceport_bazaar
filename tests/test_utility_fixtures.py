"""The agent against real states captured from a live practice run.

The states in ``tests/fixtures/`` are raw ``ServerMessage`` bytes exactly as the
server sent them during the scripted exercise, so these tests check the agent
against the real message shapes rather than ones we built to suit ourselves.

Tokens appear only in connection headers, never in a message, so the fixtures
carry no secrets.  Regenerate them with::

    python -m bazaar --policy scripted --shadow utility --capture-fixtures
"""

from pathlib import Path

import pytest

from bazaar import actions
from bazaar.brain.policy.utility import UtilityPolicy
from bazaar.validation import decode, limits, model

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> model.ServerMessage:
    return decode.decode_bytes((FIXTURES / name).read_bytes())


def state_fixtures() -> list[tuple[str, model.State]]:
    """Every captured state, in the order the server sent them."""
    found = []
    for path in sorted(FIXTURES.glob("*_state.bin")):
        found.append((path.name, decode.decode_bytes(path.read_bytes()).state))
    return found


def gift_offer(state: model.State) -> model.Offer | None:
    for offer in state.offers:
        if (
            offer.proposer_id == "P02"
            and offer.status is model.OfferStatus.OFFER_STATUS_OPEN
            and offer.receive.is_zero()
        ):
            return offer
    return None


def test_the_capture_covers_the_whole_exercise():
    """Sixteen frames, nine of them states, as the guide's counts require."""
    assert len(list(FIXTURES.glob("*.bin"))) == 16
    assert len(state_fixtures()) == 9
    assert (FIXTURES / "15_protocol_error.bin").exists()


def test_the_first_state_is_the_documented_opening_position():
    state = load("01_state.bin").state
    assert state.self_station_id == "P01"
    assert state.observation.inventory.as_tuple() == (30, 30, 30)
    assert state.observation.specialty is model.Resource.RESOURCE_WATER
    peer = [a for a in state.advertisements if a.station_id == "P02"]
    assert len(peer) == 1
    assert peer[0].selling == [model.Resource.RESOURCE_FOOD]
    assert peer[0].seeking == [model.Resource.RESOURCE_WATER]


def test_on_the_first_state_the_agent_advertises_its_water():
    """Water is the specialty, so it is always something we can sell."""
    decided = UtilityPolicy().decide(load("01_state.bin").state)
    advertises = [a for a in decided if isinstance(a, actions.Advertise)]
    assert advertises, f"expected an advertisement, got {[a.describe() for a in decided]}"
    assert model.Resource.RESOURCE_WATER in advertises[0].selling


def test_on_the_gift_state_the_agent_accepts_that_exact_offer():
    """Step 6's state carries P02's zero-price offer; it should be taken."""
    state = load("10_state.bin").state
    gift = gift_offer(state)
    assert gift is not None, "fixture 10_state.bin should hold P02's open gift"
    assert gift.give.as_tuple() == (0, 0, 1)

    decided = UtilityPolicy().decide(state)
    accepts = [a for a in decided if isinstance(a, actions.Accept)]
    assert [a.offer_id for a in accepts] == [gift.offer_id]


def test_on_the_final_state_the_agent_proposes_nothing():
    """All five stored-result slots are used, so no command may be sent."""
    state = load("16_state.bin").state
    assert len(state.request_results) == state.rules.max_request_records_per_station
    assert UtilityPolicy().decide(state) == []


@pytest.mark.parametrize("name,state", state_fixtures(), ids=[n for n, _ in state_fixtures()])
def test_every_proposed_action_is_a_legal_command(name, state):
    """Whatever the agent decides on a real state must pass the validator."""
    from bazaar.validation.commands import CommandBuilder

    builder = CommandBuilder(state.run_id)
    for index, action in enumerate(UtilityPolicy().decide(state), start=1):
        message = action.to_message(builder, f"utility-{index:04d}")
        limits.validate_command(message, state)  # raises CommandRejected on a violation


@pytest.mark.parametrize("name,state", state_fixtures(), ids=[n for n, _ in state_fixtures()])
def test_decisions_on_real_states_are_deterministic(name, state):
    assert UtilityPolicy().decide(state) == UtilityPolicy().decide(state)


def test_the_captured_protocol_error_is_the_expected_one():
    error = load("15_protocol_error.bin").protocol_error
    assert error.code is model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
    assert error.close_session is False
    assert error.request_id == "student-advertise-2"


def test_the_captured_states_decode_without_loss():
    """Every nullable and list container in a real state round-trips."""
    for name, state in state_fixtures():
        assert state.phase is model.Phase.PHASE_RUNNING
        assert isinstance(state.rules.resource_order, list)
        assert state.outcome is None or isinstance(state.outcome, model.PlayerOutcome)
