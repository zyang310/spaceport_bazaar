"""The contract every registered agent must honour, whoever wrote it.

Each test runs once per entry in ``AGENTS``, so registering a new policy is
enough to hold it to the rules in AGENTS.md -- nobody has to remember to copy
the gate and limit tests across.  What makes an agent *good* is tested in its
own file; this one only checks that it is *legal*.
"""

import dataclasses

import pytest

from bazaar import actions
from bazaar.brain.policy import AGENTS
from bazaar.brain.policy.base import BasePolicy
from bazaar.validation import limits, model
from bazaar.validation.commands import CommandBuilder

from .factories import (
    COMPONENTS,
    FOOD,
    WATER,
    advertisement,
    bundle,
    observation,
    offer,
    result,
    rules,
    state,
)

agents = pytest.mark.parametrize("agent", sorted(AGENTS.values(), key=lambda a: a.name), ids=lambda a: a.name)

#: Room to be busy: enough commands, offer slots and stored results that the
#: limits under test are the only thing holding an agent back.
ROOMY = rules(
    new_commands_per_station_per_tick=10,
    max_open_outgoing_offers=6,
    max_request_records_per_station=50,
)


def busy(**overrides) -> model.State:
    """A state where every agent has something to accept, offer and list.

    Short of food, so even Scrooge goes shopping; peers selling it; and gifts
    plus a paid deal waiting to be accepted.
    """
    defaults = dict(
        observation=observation(inventory=bundle(30, 3, 30), upkeep_per_tick=bundle(1, 1, 0)),
        advertisements=[
            advertisement(
                advertisement_id=f"ad-{station}",
                station_id=station,
                selling=(FOOD, COMPONENTS),
                seeking=(WATER, COMPONENTS),
            )
            for station in ("P02", "P03", "P04")
        ],
        offers=[
            offer(offer_id=f"gift-{n}", proposer_id="P05", give=(0, 1, 0), receive=(0, 0, 0))
            for n in range(2)
        ]
        + [offer(offer_id="deal-1", proposer_id="P06", give=(0, 2, 0), receive=(1, 0, 0))],
        rules=ROOMY,
    )
    return state(**{**defaults, **overrides})


def offers_in(decided):
    return [a for a in decided if isinstance(a, actions.Offer)]


# --- shape -----------------------------------------------------------------

@agents
def test_every_agent_extends_the_base_policy_without_replacing_decide(agent):
    assert issubclass(agent, BasePolicy)
    assert agent.decide is BasePolicy.decide
    assert AGENTS[agent.name] is agent
    assert agent().weights is agent.default_weights


@agents
def test_the_busy_state_gives_every_agent_something_to_do(agent):
    """Guards the other tests: a limit is only tested if something presses on it."""
    decided = agent().decide(busy())
    kinds = {type(action) for action in decided}
    assert {actions.Accept, actions.Offer, actions.Advertise} <= kinds


# --- gates -----------------------------------------------------------------

@agents
def test_no_actions_unless_the_run_is_running(agent):
    for phase in model.Phase:
        if phase is not model.Phase.PHASE_RUNNING:
            assert agent().decide(busy(phase=phase)) == []


@agents
def test_no_actions_from_a_failed_station(agent):
    dead = dataclasses.replace(busy().observation, health=0)
    assert agent().decide(busy(observation=dead)) == []


# --- purity and ordering -----------------------------------------------------

@agents
def test_identical_input_gives_identical_output(agent):
    built = busy()
    assert agent().decide(built) == agent().decide(built)


@agents
def test_actions_come_out_best_score_first(agent):
    scores = [action.score for action in agent().decide(busy())]
    assert scores == sorted(scores, reverse=True)


# --- limits ----------------------------------------------------------------

@agents
def test_never_more_actions_than_the_per_tick_command_limit(agent):
    tight = dataclasses.replace(ROOMY, new_commands_per_station_per_tick=2)
    assert len(agent().decide(busy(rules=tight))) == 2


@agents
def test_never_more_open_offers_than_the_rules_allow(agent):
    tight = dataclasses.replace(ROOMY, max_open_outgoing_offers=1)
    assert len(offers_in(agent().decide(busy(rules=tight)))) == 1


@agents
def test_existing_open_offers_count_against_the_offer_limit(agent):
    ours = offer(offer_id="ours", proposer_id="P01", recipient_id="P09", give=(1, 0, 0), receive=(0, 1, 0))
    tight = dataclasses.replace(ROOMY, max_open_outgoing_offers=1)
    decided = agent().decide(busy(rules=tight, offers=[*busy().offers, ours]))
    assert offers_in(decided) == []


@agents
def test_no_new_commands_once_stored_result_capacity_is_used_up(agent):
    used_up = [result(request_id=f"r{n}") for n in range(50)]
    assert agent().decide(busy(request_results=used_up)) == []


@agents
def test_remaining_capacity_caps_the_number_of_actions(agent):
    nearly = [result(request_id=f"r{n}") for n in range(49)]
    assert len(agent().decide(busy(request_results=nearly))) == 1


# --- legality --------------------------------------------------------------

@agents
def test_every_action_is_a_legal_command(agent):
    """Expiry bounds, request-ID format and size, as the runner will check them."""
    built = busy()
    builder = CommandBuilder(built.run_id)
    for index, action in enumerate(agent().decide(built), start=1):
        limits.validate_command(action.to_message(builder, f"contract-{index:04d}"), built)
