"""Hustler: volume over margin, with one floor it will not cross."""

from bazaar import actions
from bazaar.brain.policy.hustler import HustlerPolicy
from bazaar.config import HustlerWeights
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

#: Room to actually be busy: the real game allows 24 open offers, while the
#: factory default of 2 would mask the per-peer behaviour under test.
BUSY = rules(new_commands_per_station_per_tick=6, max_open_outgoing_offers=6)


def decide(**overrides):
    overrides.setdefault("rules", BUSY)
    return HustlerPolicy().decide(state(**overrides))


def peer(station="P02", selling=(FOOD, COMPONENTS), seeking=(WATER, FOOD)):
    return advertisement(
        advertisement_id=f"ad-{station}", station_id=station, selling=selling, seeking=seeking
    )


# --- gates -----------------------------------------------------------------

def test_no_actions_unless_the_run_is_running():
    for phase in model.Phase:
        if phase is not model.Phase.PHASE_RUNNING:
            assert decide(phase=phase, advertisements=[peer()]) == []


def test_no_actions_from_a_failed_station():
    assert decide(observation=observation(health=0), advertisements=[peer()]) == []


# --- always visible ---------------------------------------------------------

def test_it_advertises_even_when_it_needs_nothing():
    """A peer who cannot find us cannot trade with us."""
    ads = [a for a in decide() if isinstance(a, actions.Advertise)]
    assert len(ads) == 1 and ads[0].selling


def test_no_churn_when_the_listing_already_matches():
    first = [a for a in decide() if isinstance(a, actions.Advertise)][0]
    mine = advertisement(
        advertisement_id="mine",
        station_id="P01",
        selling=first.selling,
        seeking=first.seeking,
    )
    assert not any(isinstance(a, actions.Advertise) for a in decide(advertisements=[mine]))


# --- offers to everyone, repeatedly -----------------------------------------

def test_it_sends_several_offers_to_the_same_peer():
    offers = [a for a in decide(advertisements=[peer()]) if isinstance(a, actions.Offer)]
    assert len(offers) == 2
    assert {o.recipient_id for o in offers} == {"P02"}


def test_offers_per_peer_are_capped_and_configurable():
    one_each = HustlerPolicy(HustlerWeights(max_offers_per_peer=1))
    built = state(advertisements=[peer()], rules=BUSY)
    assert len([a for a in one_each.decide(built) if isinstance(a, actions.Offer)]) == 1


def test_existing_open_offers_count_against_the_per_peer_cap():
    mine = offer(
        offer_id="ours", proposer_id="P01", recipient_id="P02", give=(1, 0, 0), receive=(0, 1, 0)
    )
    offers = [
        a for a in decide(advertisements=[peer()], offers=[mine]) if isinstance(a, actions.Offer)
    ]
    assert len(offers) == 1  # one of the two slots for this peer is already used


def test_it_offers_to_every_peer_it_can_see():
    offers = [
        a
        for a in decide(advertisements=[peer("P03"), peer("P02")])
        if isinstance(a, actions.Offer)
    ]
    assert {o.recipient_id for o in offers} == {"P02", "P03"}


def test_peers_are_considered_in_station_order():
    decided = decide(
        advertisements=[peer("P03"), peer("P02")],
        rules=rules(new_commands_per_station_per_tick=1, max_open_outgoing_offers=1),
    )
    offers = [a for a in decided if isinstance(a, actions.Offer)]
    assert [o.recipient_id for o in offers] == ["P02"]


def test_no_offer_to_a_peer_that_wants_nothing_we_have():
    assert not any(
        isinstance(a, actions.Offer)
        for a in decide(advertisements=[peer(selling=(FOOD,), seeking=())])
    )


# --- accepting nearly everything ---------------------------------------------

def test_a_gift_is_accepted():
    decided = decide(offers=[offer(offer_id="gift-1", give=(0, 0, 1), receive=(0, 0, 0))])
    assert [a.offer_id for a in decided if isinstance(a, actions.Accept)] == ["gift-1"]


def test_an_even_trade_is_accepted():
    """Scrooge would refuse this; the Hustler wants the goods moving."""
    even = offer(offer_id="even", give=(0, 0, 1), receive=(1, 0, 0))
    assert any(isinstance(a, actions.Accept) for a in decide(offers=[even]))


def test_a_small_loss_is_accepted_as_the_cost_of_doing_business():
    slight = offer(offer_id="slight", give=(0, 0, 1), receive=(2, 0, 0))
    assert any(isinstance(a, actions.Accept) for a in decide(offers=[slight]))


def test_a_large_loss_is_still_refused():
    awful = offer(offer_id="awful", give=(0, 0, 1), receive=(9, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(offers=[awful]))


def test_the_acceptable_loss_is_configurable():
    built = state(offers=[offer(offer_id="o", give=(0, 0, 1), receive=(4, 0, 0))], rules=BUSY)
    reckless = HustlerPolicy(HustlerWeights(acceptable_loss=10.0))
    assert any(isinstance(a, actions.Accept) for a in reckless.decide(built))
    assert not any(isinstance(a, actions.Accept) for a in HustlerPolicy().decide(built))


def test_an_unaffordable_offer_is_refused():
    broke = observation(inventory=bundle(1, 1, 1))
    raid = offer(offer_id="x", give=(0, 0, 1), receive=(9, 0, 0))
    assert not any(isinstance(a, actions.Accept) for a in decide(observation=broke, offers=[raid]))


# --- the one line it holds ----------------------------------------------------

def test_it_will_not_trade_through_the_floor():
    """Three water at one upkeep a tick is already near the floor."""
    thin = observation(inventory=bundle(3, 30, 30), upkeep_per_tick=bundle(1, 1, 0))
    raid = offer(offer_id="trap", give=(0, 0, 9), receive=(2, 0, 0))
    assert not any(
        isinstance(a, actions.Accept) for a in decide(observation=thin, offers=[raid])
    )


def test_it_never_offers_what_would_take_it_through_the_floor():
    thin = observation(inventory=bundle(2, 30, 30), upkeep_per_tick=bundle(1, 1, 0))
    for action in decide(observation=thin, advertisements=[peer()]):
        if isinstance(action, actions.Offer):
            assert action.give[0] == 0  # never pays in the resource at the floor


def test_the_floor_is_configurable():
    thin = observation(inventory=bundle(3, 30, 30), upkeep_per_tick=bundle(1, 1, 0))
    built = state(
        observation=thin,
        offers=[offer(offer_id="trap", give=(0, 0, 9), receive=(2, 0, 0))],
        rules=BUSY,
    )
    daring = HustlerPolicy(HustlerWeights(floor_ticks=0.0))
    assert any(isinstance(a, actions.Accept) for a in daring.decide(built))


def test_a_resource_with_no_upkeep_never_blocks_a_trade():
    no_upkeep = observation(inventory=bundle(30, 30, 1), upkeep_per_tick=bundle(1, 1, 0))
    spend = offer(offer_id="ok", give=(5, 0, 0), receive=(0, 0, 1))
    assert any(
        isinstance(a, actions.Accept) for a in decide(observation=no_upkeep, offers=[spend])
    )


# --- limits and determinism ----------------------------------------------------

def test_it_spends_the_whole_command_budget():
    decided = decide(
        advertisements=[peer("P02"), peer("P03")],
        rules=rules(new_commands_per_station_per_tick=3),
    )
    assert len(decided) == 3


def test_no_new_commands_once_stored_result_capacity_is_used_up():
    assert (
        decide(
            advertisements=[peer()],
            request_results=[result(request_id=f"r{n}") for n in range(5)],
            rules=rules(max_request_records_per_station=5),
        )
        == []
    )


def test_never_more_open_offers_than_the_rules_allow():
    decided = decide(
        advertisements=[peer("P02"), peer("P03")],
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=6),
    )
    assert len([a for a in decided if isinstance(a, actions.Offer)]) == 1


def test_offers_outrank_listings():
    decided = decide(advertisements=[peer()], rules=rules(new_commands_per_station_per_tick=1))
    assert isinstance(decided[0], actions.Offer)


def test_identical_input_gives_identical_output():
    built = state(
        advertisements=[peer("P03"), peer("P02")],
        offers=[offer(offer_id="gift-1"), offer(offer_id="gift-0")],
        rules=BUSY,
    )
    assert HustlerPolicy().decide(built) == HustlerPolicy().decide(built)


def test_it_trades_far_more_than_scrooge_on_the_same_state():
    """The two agents' theses, side by side on one comfortable position."""
    from bazaar.brain.policy.scrooge import ScroogePolicy

    built = state(advertisements=[peer()], rules=BUSY)
    assert len(HustlerPolicy().decide(built)) > len(ScroogePolicy().decide(built))
