"""The agent's decisions, against states built to order.

``decide`` is a pure function of the state it is handed, so every case here is
a state built in code and a list of actions compared against it.
"""

from bazaar import actions
from bazaar.brain.policy.utility import UtilityPolicy
from bazaar.config import PolicyWeights
from bazaar.validation import model

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


def decide(**overrides):
    return UtilityPolicy().decide(state(**overrides))


def kinds(decided):
    return [type(action).__name__ for action in decided]


# --- gates -----------------------------------------------------------------

def test_no_actions_unless_the_run_is_running():
    for phase in model.Phase:
        if phase is model.Phase.PHASE_RUNNING:
            continue
        assert decide(phase=phase, advertisements=[advertisement()]) == []


def test_no_actions_from_a_failed_station():
    """Zero health is permanent, so there is nothing to trade toward."""
    assert decide(observation=observation(health=0), advertisements=[advertisement()]) == []


# --- accepting -------------------------------------------------------------

def test_a_zero_price_gift_is_accepted():
    gift = offer(offer_id="gift-1", give=(0, 0, 1), receive=(0, 0, 0))
    decided = decide(offers=[gift])
    accepts = [a for a in decided if isinstance(a, actions.Accept)]
    assert [a.offer_id for a in accepts] == ["gift-1"]


def test_a_gift_is_accepted_even_when_we_need_nothing():
    """A free component is still free, whatever our projections say."""
    rich = observation(inventory=bundle(99, 99, 99))
    decided = decide(observation=rich, offers=[offer(offer_id="gift-1")])
    assert any(isinstance(a, actions.Accept) for a in decided)


def test_an_offer_creating_a_deficit_is_refused_despite_a_good_price():
    # Water sits just above its projected need, so paying three would drop us
    # under it -- even though five components is worth far more to us.
    lean = observation(inventory=bundle(8, 30, 0), upkeep_per_tick=bundle(1, 1, 0))
    tempting = offer(offer_id="trap-1", give=(0, 0, 5), receive=(3, 0, 0))
    decided = decide(observation=lean, offers=[tempting])
    assert not any(isinstance(a, actions.Accept) for a in decided)


def test_an_unaffordable_offer_is_refused():
    broke = observation(inventory=bundle(1, 1, 1))
    decided = decide(observation=broke, offers=[offer(offer_id="x", give=(0, 0, 5), receive=(9, 0, 0))])
    assert not any(isinstance(a, actions.Accept) for a in decided)


def test_offers_addressed_to_someone_else_are_ignored():
    theirs = offer(offer_id="not-ours", recipient_id="P03", give=(0, 0, 1), receive=(0, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(offers=[theirs]))


def test_a_closed_offer_is_ignored():
    closed = offer(offer_id="done", status=model.OfferStatus.OFFER_STATUS_ACCEPTED)
    assert not any(isinstance(a, actions.Accept) for a in decide(offers=[closed]))


# --- advertising -----------------------------------------------------------

def test_the_specialty_is_advertised_as_something_we_sell():
    decided = decide()
    ads = [a for a in decided if isinstance(a, actions.Advertise)]
    assert len(ads) == 1
    assert WATER in ads[0].selling


def test_a_deficit_is_advertised_as_something_we_seek():
    hungry = observation(inventory=bundle(30, 0, 30), upkeep_per_tick=bundle(1, 1, 0))
    ads = [a for a in decide(observation=hungry) if isinstance(a, actions.Advertise)]
    assert ads and FOOD in ads[0].seeking and FOOD not in ads[0].selling


def test_no_duplicate_advertisement_when_the_listing_already_matches():
    mine = advertisement(
        advertisement_id="mine", station_id="P01", selling=(WATER, FOOD, COMPONENTS), seeking=()
    )
    decided = decide(advertisements=[mine])
    assert not any(isinstance(a, actions.Advertise) for a in decided)


def test_a_stale_advertisement_is_replaced():
    stale = advertisement(advertisement_id="mine", station_id="P01", selling=(FOOD,), seeking=())
    decided = decide(advertisements=[stale])
    assert any(isinstance(a, actions.Advertise) for a in decided)


def test_the_advertisement_expiry_respects_the_rules_ceiling():
    tight = rules(max_publication_ttl_ticks=2)
    ads = [a for a in decide(rules=tight, tick=3) if isinstance(a, actions.Advertise)]
    assert ads and ads[0].expires_tick == 5  # tick 3 + the 2-tick ceiling


# --- offering --------------------------------------------------------------

def peer_selling_food():
    return advertisement(advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(WATER,))


def hungry_observation():
    return observation(inventory=bundle(30, 0, 30), upkeep_per_tick=bundle(1, 1, 0))


def test_an_offer_is_proposed_to_a_peer_selling_what_we_need():
    decided = decide(observation=hungry_observation(), advertisements=[peer_selling_food()])
    offers = [a for a in decided if isinstance(a, actions.Offer)]
    assert len(offers) == 1
    proposed = offers[0]
    assert proposed.recipient_id == "P02"
    assert proposed.receive == (0, 1, 0)  # one food, which we are short of
    assert proposed.give == (2, 0, 0)  # paid in surplus water at the 2:1 ratio


def test_no_offer_to_a_peer_we_already_have_one_open_with():
    mine = offer(offer_id="ours", proposer_id="P01", recipient_id="P02", give=(2, 0, 0), receive=(0, 1, 0))
    decided = decide(
        observation=hungry_observation(), advertisements=[peer_selling_food()], offers=[mine]
    )
    assert not any(isinstance(a, actions.Offer) for a in decided)


def test_no_offer_when_the_peer_seeks_nothing_we_can_spare():
    useless = advertisement(station_id="P02", selling=(FOOD,), seeking=(FOOD,))
    decided = decide(observation=hungry_observation(), advertisements=[useless])
    assert not any(isinstance(a, actions.Offer) for a in decided)


# --- withdrawing -----------------------------------------------------------

def test_an_offer_that_turned_negative_is_withdrawn():
    # We would pay five components we now need, for water we already have spare.
    sour = offer(
        offer_id="sour-1", proposer_id="P01", recipient_id="P02", give=(0, 0, 5), receive=(1, 0, 0)
    )
    lean = observation(inventory=bundle(30, 30, 0), upkeep_per_tick=bundle(1, 1, 1))
    decided = decide(observation=lean, offers=[sour])
    assert any(isinstance(a, actions.Withdraw) and a.object_id == "sour-1" for a in decided)


# --- limits ----------------------------------------------------------------

def test_never_more_actions_than_the_per_tick_command_limit():
    gifts = [offer(offer_id=f"gift-{n}", give=(0, 0, 1), receive=(0, 0, 0)) for n in range(4)]
    decided = decide(rules=rules(new_commands_per_station_per_tick=2), offers=gifts)
    assert len(decided) == 2


def test_never_more_open_offers_than_the_rules_allow():
    peers = [
        advertisement(advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(WATER,)),
        advertisement(advertisement_id="p03", station_id="P03", selling=(FOOD,), seeking=(WATER,)),
    ]
    decided = decide(
        observation=hungry_observation(),
        advertisements=peers,
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=3),
    )
    assert len([a for a in decided if isinstance(a, actions.Offer)]) == 1


def test_existing_open_offers_count_against_the_limit():
    mine = offer(offer_id="ours", proposer_id="P01", recipient_id="P09", give=(2, 0, 0), receive=(0, 1, 0))
    decided = decide(
        observation=hungry_observation(),
        advertisements=[peer_selling_food()],
        offers=[mine],
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=3),
    )
    assert not any(isinstance(a, actions.Offer) for a in decided)


def test_no_new_commands_once_stored_result_capacity_is_used_up():
    used_up = [result(request_id=f"r{n}") for n in range(5)]
    decided = decide(
        request_results=used_up,
        rules=rules(max_request_records_per_station=5),
        offers=[offer(offer_id="gift-1")],
        advertisements=[advertisement()],
    )
    assert decided == []


def test_remaining_capacity_caps_the_number_of_actions():
    gifts = [offer(offer_id=f"gift-{n}", give=(0, 0, 1), receive=(0, 0, 0)) for n in range(4)]
    decided = decide(
        request_results=[result(request_id=f"r{n}") for n in range(4)],
        rules=rules(max_request_records_per_station=5, new_commands_per_station_per_tick=3),
        offers=gifts,
    )
    assert len(decided) == 1  # one slot left, so one command


# --- determinism -----------------------------------------------------------

def test_identical_input_gives_identical_output():
    built = state(
        observation=hungry_observation(),
        advertisements=[
            advertisement(advertisement_id="p03", station_id="P03", selling=(FOOD,), seeking=(WATER,)),
            advertisement(advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(WATER,)),
        ],
        offers=[offer(offer_id="gift-1"), offer(offer_id="gift-0")],
    )
    first = UtilityPolicy().decide(built)
    second = UtilityPolicy().decide(built)
    assert first == second
    assert [a.describe() for a in first] == [a.describe() for a in second]


def test_peers_are_considered_in_station_order():
    peers = [
        advertisement(advertisement_id="p03", station_id="P03", selling=(FOOD,), seeking=(WATER,)),
        advertisement(advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(WATER,)),
    ]
    decided = decide(
        observation=hungry_observation(),
        advertisements=peers,
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=3),
    )
    offers = [a for a in decided if isinstance(a, actions.Offer)]
    assert [o.recipient_id for o in offers] == ["P02"]  # lowest station ID wins the slot


def test_weights_are_configurable_without_touching_the_policy():
    generous = UtilityPolicy(PolicyWeights(offer_ratio=5))
    built = state(observation=hungry_observation(), advertisements=[peer_selling_food()])
    offers = [a for a in generous.decide(built) if isinstance(a, actions.Offer)]
    assert offers and offers[0].give == (5, 0, 0)
