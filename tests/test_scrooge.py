"""Scrooge: reserves first, specialty as currency, charity never.

``decide`` is a pure function of the state it is handed, so every case here is
a state built in code and a list of actions compared against it.
"""

from bazaar import actions
from bazaar.brain.policy.scrooge import ScroogePolicy
from bazaar.config import ScroogeWeights
from bazaar.validation import model

from .factories import (
    COMPONENTS,
    FOOD,
    WATER,
    advertisement,
    bundle,
    observation,
    offer,
    rules,
    state,
)

#: Comfortable on water and components, running out of food. Specialty water.
SHORT_OF_FOOD = observation(inventory=bundle(30, 3, 30), upkeep_per_tick=bundle(1, 1, 0))
#: Everything running out at once, specialty included.
DESTITUTE = observation(inventory=bundle(2, 2, 2), upkeep_per_tick=bundle(1, 1, 1))


def decide(**overrides):
    return ScroogePolicy().decide(state(**overrides))


def sells_food_wants_water(station="P02"):
    return advertisement(
        advertisement_id=f"ad-{station}", station_id=station, selling=(FOOD,), seeking=(WATER,)
    )


# --- charity: never -------------------------------------------------------

def test_it_never_proposes_a_gift():
    """No offer it makes may have an empty receive, in any situation."""
    for obs in (observation(), SHORT_OF_FOOD, DESTITUTE):
        for ads in ([], [sells_food_wants_water()]):
            for action in decide(observation=obs, advertisements=ads):
                if isinstance(action, actions.Offer):
                    assert model.Bundle.of(action.receive).as_tuple() != (0, 0, 0)


def test_it_never_advertises_without_seeking_something():
    """A listing that only sells is an advertisement for charity."""
    for obs in (observation(), SHORT_OF_FOOD, DESTITUTE):
        for action in decide(observation=obs, advertisements=[sells_food_wants_water()]):
            if isinstance(action, actions.Advertise):
                assert action.seeking


def test_a_gift_arriving_the_other_way_is_still_taken():
    """Scrooge does not give. He certainly takes."""
    decided = decide(offers=[offer(offer_id="gift-1", give=(0, 0, 1), receive=(0, 0, 0))])
    assert [a.offer_id for a in decided if isinstance(a, actions.Accept)] == ["gift-1"]


# --- posting only under pressure -------------------------------------------

def test_it_posts_nothing_while_comfortable():
    """Above the danger line it reads its position and stays quiet."""
    assert decide(advertisements=[sells_food_wants_water()]) == []


def test_a_shortage_makes_it_go_shopping():
    decided = decide(observation=SHORT_OF_FOOD, advertisements=[sells_food_wants_water()])
    kinds = {type(a).__name__ for a in decided}
    assert "Offer" in kinds and "Advertise" in kinds


def test_the_listing_seeks_what_is_running_out():
    ads = [a for a in decide(observation=SHORT_OF_FOOD, advertisements=[sells_food_wants_water()])
           if isinstance(a, actions.Advertise)]
    assert ads and ads[0].seeking == (FOOD,)


def test_the_danger_threshold_is_configurable():
    """Raise the line and a position that was comfortable becomes urgent."""
    jumpy = ScroogePolicy(ScroogeWeights(danger_ticks=40.0))
    built = state(advertisements=[sells_food_wants_water()])
    assert jumpy.decide(built) != []
    assert ScroogePolicy().decide(built) == []


# --- the specialty is the currency ------------------------------------------

def test_it_pays_in_its_specialty():
    offers = [a for a in decide(observation=SHORT_OF_FOOD, advertisements=[sells_food_wants_water()])
              if isinstance(a, actions.Offer)]
    assert offers
    assert offers[0].give == (2, 0, 0)  # water, the specialty, at the 2:1 ratio
    assert offers[0].receive == (0, 1, 0)


def test_it_advertises_its_specialty_first_among_what_it_will_part_with():
    """Everything spare is listed, but the specialty leads it."""
    ads = [a for a in decide(observation=SHORT_OF_FOOD, advertisements=[sells_food_wants_water()])
           if isinstance(a, actions.Advertise)]
    assert ads and ads[0].selling[0] == WATER
    assert FOOD not in ads[0].selling  # never the thing we are short of


def test_it_falls_back_to_a_second_coin_the_peer_will_take():
    """Specialty first, but a peer wanting components is not turned away."""
    wants_components = advertisement(
        advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(COMPONENTS,)
    )
    offers = [a for a in decide(observation=SHORT_OF_FOOD, advertisements=[wants_components])
              if isinstance(a, actions.Offer)]
    assert offers and offers[0].give == (0, 0, 2)


def test_it_does_not_offer_to_a_peer_that_will_take_nothing_we_can_spare():
    """The only coin they want is the very thing we are short of."""
    wants_food = advertisement(
        advertisement_id="p02", station_id="P02", selling=(FOOD,), seeking=(FOOD,)
    )
    decided = decide(observation=SHORT_OF_FOOD, advertisements=[wants_food])
    assert not any(isinstance(a, actions.Offer) for a in decided)


def test_it_pays_with_something_else_when_the_specialty_is_the_thing_running_out():
    """Water is both the specialty and the shortage, so components pay instead."""
    short_of_water = observation(inventory=bundle(2, 30, 30), upkeep_per_tick=bundle(1, 1, 0))
    peer = advertisement(
        advertisement_id="p02", station_id="P02", selling=(WATER,), seeking=(COMPONENTS,)
    )
    offers = [a for a in decide(observation=short_of_water, advertisements=[peer])
              if isinstance(a, actions.Offer)]
    assert offers and offers[0].give == (0, 0, 2) and offers[0].receive == (1, 0, 0)
    assert offers[0].give[0] == 0  # never paid in the resource we are short of


# --- accepting: picky until it hurts ----------------------------------------

def test_while_comfortable_it_only_accepts_trades_priced_in_its_specialty():
    """Nine components for two food is a fine deal, but food is not our coin."""
    priced_in_food = offer(offer_id="o1", give=(0, 0, 9), receive=(0, 2, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(offers=[priced_in_food]))


def test_while_comfortable_a_clear_profit_in_its_specialty_is_taken():
    priced_in_water = offer(offer_id="o2", give=(0, 0, 9), receive=(2, 0, 0))
    decided = decide(offers=[priced_in_water])
    assert [a.offer_id for a in decided if isinstance(a, actions.Accept)] == ["o2"]


def test_while_comfortable_a_merely_fair_trade_is_refused():
    """The bar is a multiple, not a margin: fair is not good enough."""
    fair = offer(offer_id="o3", give=(0, 0, 1), receive=(2, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(offers=[fair]))


def test_the_advantage_ratio_is_configurable():
    lenient = ScroogePolicy(ScroogeWeights(advantage_ratio=1.0))
    built = state(offers=[offer(offer_id="o4", give=(0, 0, 1), receive=(2, 0, 0))])
    assert any(isinstance(a, actions.Accept) for a in lenient.decide(built))
    assert not any(isinstance(a, actions.Accept) for a in ScroogePolicy().decide(built))


def test_under_pressure_it_will_pay_in_something_other_than_its_specialty():
    """Once food is running out, a profitable trade in any coin is worth taking."""
    rescue = offer(offer_id="o5", give=(0, 5, 0), receive=(0, 0, 4))
    decided = decide(observation=SHORT_OF_FOOD, offers=[rescue])
    assert [a.offer_id for a in decided if isinstance(a, actions.Accept)] == ["o5"]


# --- reserves are the thing being defended ----------------------------------

def test_it_refuses_a_trade_that_would_drop_it_below_the_danger_line():
    """Twelve components is a lot, but not if it costs us our water cover."""
    thin = observation(inventory=bundle(6, 30, 0), upkeep_per_tick=bundle(1, 1, 0))
    raid = offer(offer_id="trap", give=(0, 0, 12), receive=(5, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(observation=thin, offers=[raid]))


def test_an_unaffordable_offer_is_refused():
    broke = observation(inventory=bundle(1, 1, 1), upkeep_per_tick=bundle(1, 1, 0))
    raid = offer(offer_id="x", give=(0, 0, 9), receive=(9, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(observation=broke, offers=[raid]))


def test_a_resource_with_no_upkeep_is_never_in_danger():
    """Nothing consumes it, so no stock level makes it urgent."""
    policy = ScroogePolicy()
    built = state(observation=observation(inventory=bundle(30, 30, 0), upkeep_per_tick=bundle(1, 1, 0)))
    assert COMPONENTS not in policy.critical(built, policy.cover(built))


# --- withdrawing -------------------------------------------------------------

def test_it_takes_down_its_listing_once_the_shortage_passes():
    mine = advertisement(
        advertisement_id="mine", station_id="P01", selling=(WATER,), seeking=(FOOD,)
    )
    decided = decide(advertisements=[mine])
    assert any(isinstance(a, actions.Withdraw) and a.object_id == "mine" for a in decided)


def test_it_takes_down_a_listing_that_asks_for_nothing_in_return():
    charity = advertisement(
        advertisement_id="mine", station_id="P01", selling=(WATER,), seeking=()
    )
    decided = decide(observation=SHORT_OF_FOOD, advertisements=[charity])
    assert any(isinstance(a, actions.Withdraw) and a.object_id == "mine" for a in decided)


def test_no_churn_when_the_listing_already_says_the_right_thing():
    mine = advertisement(
        advertisement_id="mine",
        station_id="P01",
        selling=(WATER, COMPONENTS),
        seeking=(FOOD,),
    )
    decided = decide(observation=SHORT_OF_FOOD, advertisements=[mine, sells_food_wants_water()])
    assert not any(isinstance(a, actions.Advertise) for a in decided)


# --- horizon -----------------------------------------------------------------

def test_the_horizon_is_longer_than_the_utility_agents():
    from bazaar.config import DEFAULT_SCROOGE, DEFAULT_WEIGHTS

    assert DEFAULT_SCROOGE.horizon > DEFAULT_WEIGHTS.horizon


def test_the_horizon_never_reaches_past_the_end_of_the_run():
    policy = ScroogePolicy()
    near_the_end = state(tick=10, rules=rules(duration_ticks=12))
    assert policy.horizon(near_the_end) == 2
    assert policy.horizon(state(tick=0, rules=rules(duration_ticks=100))) == DEFAULT_HORIZON


def test_the_horizon_never_collapses_to_zero():
    """Past the final tick the lookahead floors at one, not nothing."""
    assert ScroogePolicy().horizon(state(tick=99, rules=rules(duration_ticks=12))) == 1


def test_capping_the_horizon_can_be_switched_off():
    uncapped = ScroogePolicy(ScroogeWeights(cap_horizon_to_run=False))
    assert uncapped.horizon(state(tick=10, rules=rules(duration_ticks=12))) == DEFAULT_HORIZON


DEFAULT_HORIZON = ScroogeWeights().horizon


# --- determinism -------------------------------------------------------------

def test_peers_are_considered_in_station_order():
    peers = [sells_food_wants_water("P03"), sells_food_wants_water("P02")]
    decided = decide(
        observation=SHORT_OF_FOOD,
        advertisements=peers,
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=5),
    )
    offers = [a for a in decided if isinstance(a, actions.Offer)]
    assert [o.recipient_id for o in offers] == ["P02"]
