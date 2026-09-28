"""Hivemind: the coordinator's shared-reserve allocator, from one station."""

from bazaar import actions
from bazaar.brain.policy import AGENTS
from bazaar.brain.policy.hivemind import HivemindPolicy
from bazaar.config import HivemindWeights
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

#: Room to be busy.  The factory default of two offers would hide the passes.
BUSY = rules(new_commands_per_station_per_tick=6, max_open_outgoing_offers=6)
#: Everything a station pays each tick, as in the live game.
FULL_UPKEEP = bundle(1, 1, 1)


def decide(**overrides):
    overrides.setdefault("rules", BUSY)
    return HivemindPolicy().decide(state(**overrides))


def of_type(decided, kind):
    return [a for a in decided if isinstance(a, kind)]


def peer(station="P02", selling=(FOOD,), seeking=(WATER,)):
    return advertisement(
        advertisement_id=f"ad-{station}", station_id=station, selling=selling, seeking=seeking
    )


def short_of_food(food=0):
    """Plenty of water, little food, and every resource costs one a tick."""
    return observation(inventory=bundle(30, food, 30), upkeep_per_tick=FULL_UPKEEP)


def exchanges(decided):
    return [o for o in of_type(decided, actions.Offer) if any(o.receive)]


def gifts(decided):
    return [o for o in of_type(decided, actions.Offer) if not any(o.receive)]


# --- gates -----------------------------------------------------------------

def test_no_actions_unless_the_run_is_running():
    for phase in model.Phase:
        if phase is not model.Phase.PHASE_RUNNING:
            assert decide(phase=phase, advertisements=[peer()]) == []


def test_no_actions_from_a_failed_station():
    assert decide(observation=observation(health=0), advertisements=[peer()]) == []


def test_a_station_that_has_failed_once_stays_out():
    """The coordinator drops a station for good once it has failed."""
    scarred = observation(health=50, failed_once=True)
    assert decide(observation=scarred, advertisements=[peer()]) == []


def test_it_is_registered_for_the_cli():
    assert AGENTS["hivemind"] is HivemindPolicy


# --- reserve math ------------------------------------------------------------

def test_the_reserve_is_reserve_ticks_of_upkeep():
    built = state(observation=observation(upkeep_per_tick=bundle(2, 1, 0)))
    assert HivemindPolicy().target(built) == {WATER: 8, FOOD: 4, COMPONENTS: 0}


def test_open_offers_are_deducted_before_anything_counts_as_spare():
    """Water 10, reserve 4, and 5 already promised: only 1 is spare."""
    promised = offer(offer_id="ours", proposer_id="P01", recipient_id="P03", give=(5, 0, 0))
    built = state(
        observation=observation(inventory=bundle(10, 30, 30)),
        offers=[promised],
        advertisements=[peer(seeking=(WATER,))],
        rules=BUSY,
    )
    assert [g.give for g in gifts(HivemindPolicy().decide(built))] == [(1, 0, 0)]


# --- accepting ---------------------------------------------------------------

def test_a_gift_is_accepted_even_with_nothing_to_spare():
    broke = observation(inventory=bundle(0, 0, 0))
    gift = offer(offer_id="gift-1", give=(0, 0, 1), receive=(0, 0, 0))
    decided = decide(observation=broke, offers=[gift])
    assert [a.offer_id for a in of_type(decided, actions.Accept)] == ["gift-1"]


def test_a_complementary_paid_offer_is_accepted():
    fair = offer(offer_id="fair", give=(0, 2, 0), receive=(3, 0, 0))
    decided = decide(observation=short_of_food(food=1), offers=[fair])
    assert [a.offer_id for a in of_type(decided, actions.Accept)] == ["fair"]


def test_a_paid_offer_that_would_dip_into_the_reserve_is_refused():
    """Six water against a reserve of four leaves two spare, not three."""
    thin = observation(inventory=bundle(6, 1, 30), upkeep_per_tick=FULL_UPKEEP)
    dear = offer(offer_id="dear", give=(0, 2, 0), receive=(3, 0, 0))
    assert of_type(decide(observation=thin, offers=[dear]), actions.Accept) == []


def test_a_paid_offer_that_brings_nothing_we_need_is_refused():
    pointless = offer(offer_id="pointless", give=(0, 2, 0), receive=(3, 0, 0))
    assert of_type(decide(offers=[pointless]), actions.Accept) == []


def test_two_paid_offers_cannot_spend_the_same_spare_stock():
    """Three spare water covers one of these, not both."""
    thin = observation(inventory=bundle(7, 1, 30), upkeep_per_tick=FULL_UPKEEP)
    first = offer(offer_id="a", give=(0, 1, 0), receive=(2, 0, 0))
    second = offer(offer_id="b", give=(0, 1, 0), receive=(2, 0, 0))
    decided = decide(observation=thin, offers=[second, first])
    assert [a.offer_id for a in of_type(decided, actions.Accept)] == ["a"]


def test_an_accepted_gift_is_counted_before_asking_peers_for_more():
    gift = offer(offer_id="gift", proposer_id="P03", give=(0, 2, 0), receive=(0, 0, 0))
    assert exchanges(decide(observation=short_of_food(), advertisements=[peer()])) != []
    assert (
        exchanges(decide(observation=short_of_food(), advertisements=[peer()], offers=[gift]))
        == []
    )


# --- exchanges ---------------------------------------------------------------

def test_an_exchange_trades_spare_stock_for_what_we_are_short_of():
    [made] = exchanges(decide(observation=short_of_food(), advertisements=[peer()]))
    assert made.recipient_id == "P02"
    assert made.give == (2, 0, 0) and made.receive == (0, 2, 0)


def test_an_exchange_only_fills_the_delivery_window():
    """One food on hand, a two-tick window: ask for one, not the whole reserve."""
    [made] = exchanges(decide(observation=short_of_food(food=1), advertisements=[peer()]))
    assert made.receive == (0, 1, 0)


def test_no_exchange_while_we_hold_more_than_the_delivery_window():
    assert exchanges(decide(observation=short_of_food(food=3), advertisements=[peer()])) == []


def test_the_delivery_window_is_configurable():
    wide = HivemindPolicy(HivemindWeights(survival_ticks=4))
    built = state(observation=short_of_food(food=1), advertisements=[peer()], rules=BUSY)
    [made] = exchanges(wide.decide(built))
    assert made.receive == (0, 3, 0)


def test_a_shortage_is_asked_for_once_per_decision():
    decided = decide(observation=short_of_food(), advertisements=[peer("P02"), peer("P03")])
    assert [o.recipient_id for o in exchanges(decided)] == ["P02"]


def test_an_open_exchange_is_not_repeated_to_the_same_peer():
    ours = offer(
        offer_id="ours", proposer_id="P01", recipient_id="P02", give=(2, 0, 0), receive=(0, 2, 0)
    )
    assert exchanges(decide(observation=short_of_food(), advertisements=[peer()], offers=[ours])) == []


def test_a_pending_ask_counts_only_for_the_delivery_window():
    """After two ticks unanswered, we look for another seller."""
    ours = offer(
        offer_id="ours",
        proposer_id="P01",
        recipient_id="P02",
        give=(2, 0, 0),
        receive=(0, 2, 0),
        expires_tick=10,
    )
    sellers = [peer("P02"), peer("P03")]
    fresh = decide(observation=short_of_food(), advertisements=sellers, offers=[ours], tick=1)
    stale = decide(observation=short_of_food(), advertisements=sellers, offers=[ours], tick=2)
    assert exchanges(fresh) == []
    assert [o.recipient_id for o in exchanges(stale)] == ["P03"]


def test_exchanges_come_before_aid():
    decided = decide(
        observation=short_of_food(),
        advertisements=[peer()],
        rules=rules(new_commands_per_station_per_tick=1, max_open_outgoing_offers=6),
    )
    assert decided == exchanges(decided) and len(decided) == 1


# --- aid -----------------------------------------------------------------------

def test_spare_stock_is_given_to_a_peer_that_seeks_it():
    [gift] = gifts(decide(advertisements=[peer(seeking=(WATER,))]))
    assert gift.recipient_id == "P02" and gift.receive == (0, 0, 0)


def test_aid_fills_at_most_one_reserve_for_that_peer():
    """The peer's upkeep is assumed to match ours: one water a tick, four ticks."""
    [gift] = gifts(decide(advertisements=[peer(seeking=(WATER,))]))
    assert gift.give == (4, 0, 0)


def test_aid_never_digs_into_our_own_reserve():
    thin = observation(inventory=bundle(5, 30, 30))
    [gift] = gifts(decide(observation=thin, advertisements=[peer(seeking=(WATER,))]))
    assert gift.give == (1, 0, 0)


def test_no_aid_from_a_resource_at_or_below_its_reserve():
    at_reserve = observation(inventory=bundle(4, 30, 30))
    assert gifts(decide(observation=at_reserve, advertisements=[peer(seeking=(WATER,))])) == []


def test_aid_already_on_its_way_to_a_peer_is_not_repeated():
    theirs = offer(offer_id="theirs", proposer_id="P03", recipient_id="P02", give=(3, 0, 0))
    [gift] = gifts(decide(advertisements=[peer(seeking=(WATER,))], offers=[theirs]))
    assert gift.give == (1, 0, 0)


def test_the_largest_gift_goes_first():
    decided = decide(
        advertisements=[peer("P02", seeking=(WATER,)), peer("P03", seeking=(WATER, FOOD))]
    )
    assert [g.recipient_id for g in gifts(decided)] == ["P03", "P02"]


def test_equal_gifts_go_in_station_order():
    decided = decide(advertisements=[peer("P03"), peer("P02")])
    assert [g.recipient_id for g in gifts(decided)] == ["P02", "P03"]


def test_offers_use_the_full_lifetime_the_rules_allow():
    [gift] = gifts(decide(advertisements=[peer()], tick=3))
    assert gift.expires_tick == 3 + BUSY.max_offer_ttl_ticks


# --- withdrawing ------------------------------------------------------------------

def test_an_offer_that_would_now_leave_less_than_one_upkeep_is_withdrawn():
    drained = observation(inventory=bundle(3, 30, 30))
    ours = offer(offer_id="ours", proposer_id="P01", recipient_id="P02", give=(3, 0, 0))
    decided = decide(observation=drained, offers=[ours])
    assert [w.object_id for w in of_type(decided, actions.Withdraw)] == ["ours"]


def test_an_offer_that_still_fits_is_left_alone():
    drained = observation(inventory=bundle(3, 30, 30))
    ours = offer(offer_id="ours", proposer_id="P01", recipient_id="P02", give=(2, 0, 0))
    assert of_type(decide(observation=drained, offers=[ours]), actions.Withdraw) == []


# --- listing ----------------------------------------------------------------------

def test_it_lists_what_is_above_and_below_its_reserve():
    [listing] = of_type(decide(observation=short_of_food(food=1)), actions.Advertise)
    assert listing.selling == (WATER, COMPONENTS)
    assert listing.seeking == (FOOD,)


def test_no_churn_when_the_listing_already_matches():
    mine = advertisement(
        advertisement_id="mine", station_id="P01", selling=(WATER, COMPONENTS), seeking=(FOOD,)
    )
    decided = decide(observation=short_of_food(food=1), advertisements=[mine])
    assert of_type(decided, actions.Advertise) == []


def test_a_lapsed_listing_is_republished():
    mine = advertisement(
        advertisement_id="mine",
        station_id="P01",
        selling=(WATER, COMPONENTS),
        seeking=(FOOD,),
        expires_tick=6,
    )
    decided = decide(observation=short_of_food(food=1), advertisements=[mine], tick=6)
    assert len(of_type(decided, actions.Advertise)) == 1


def test_a_listing_with_nothing_to_say_is_withdrawn():
    level = observation(inventory=bundle(4, 4, 0))
    mine = advertisement(advertisement_id="mine", station_id="P01")
    decided = decide(observation=level, advertisements=[mine])
    assert [w.object_id for w in of_type(decided, actions.Withdraw)] == ["mine"]


# --- limits and determinism --------------------------------------------------------

def test_the_tick_budget_counts_commands_already_sent_this_tick():
    many_peers = [peer(f"P0{n}") for n in range(2, 7)]
    this_tick = [result(request_id=f"r{n}", processed_tick=4) for n in range(2)]
    last_tick = [result(request_id=f"r{n}", processed_tick=3) for n in range(2)]
    tight = rules(new_commands_per_station_per_tick=3, max_open_outgoing_offers=6)
    assert len(decide(advertisements=many_peers, request_results=this_tick, tick=4, rules=tight)) == 1
    assert len(decide(advertisements=many_peers, request_results=last_tick, tick=4, rules=tight)) == 3


def test_no_new_commands_once_stored_result_capacity_is_used_up():
    """The results are from an earlier tick, so only capacity can be the limit."""
    assert (
        decide(
            advertisements=[peer()],
            request_results=[result(request_id=f"r{n}", processed_tick=0) for n in range(5)],
            rules=rules(max_request_records_per_station=5),
            tick=1,
        )
        == []
    )


def test_never_more_open_offers_than_the_rules_allow():
    decided = decide(
        advertisements=[peer("P02"), peer("P03")],
        rules=rules(max_open_outgoing_offers=1, new_commands_per_station_per_tick=6),
    )
    assert len(of_type(decided, actions.Offer)) == 1


def test_identical_input_gives_identical_output():
    built = state(
        observation=short_of_food(food=1),
        advertisements=[peer("P03"), peer("P02", seeking=(WATER, COMPONENTS))],
        offers=[offer(offer_id="gift-1"), offer(offer_id="gift-0")],
        rules=BUSY,
    )
    assert HivemindPolicy().decide(built) == HivemindPolicy().decide(built)
