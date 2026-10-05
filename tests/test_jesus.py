"""Jesus: trade freely, keep enough, give the rest away."""

from bazaar import actions
from bazaar.brain.policy.jesus import JesusPolicy
from bazaar.config import JesusWeights

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

#: Room to actually be busy: the real game allows 24 open offers, while the
#: factory default of 2 would mask the per-peer behaviour under test.
BUSY = rules(new_commands_per_station_per_tick=6, max_open_outgoing_offers=6)

#: Something worth buying.  The factory's 30 of everything is more than
#: ``enough_ticks`` of cover, and an agent that holds enough rightly buys none.
HUNGRY = observation(inventory=bundle(30, 10, 10), upkeep_per_tick=bundle(1, 1, 1))


def build(**overrides):
    overrides.setdefault("rules", BUSY)
    overrides.setdefault("observation", HUNGRY)
    return state(**overrides)


def decide(**overrides):
    return JesusPolicy().decide(build(**overrides))


def peer(station="P02", selling=(FOOD, COMPONENTS), seeking=(WATER, FOOD)):
    return advertisement(
        advertisement_id=f"ad-{station}", station_id=station, selling=selling, seeking=seeking
    )


def of(decided, kind):
    return [a for a in decided if isinstance(a, kind)]


def listing(decided):
    return of(decided, actions.Advertise)[0]


def offers_in(decided):
    """Offers that ask for something back; gifts are counted separately."""
    return [a for a in of(decided, actions.Offer) if any(a.receive)]


def gifts_in(decided):
    return [a for a in of(decided, actions.Offer) if not any(a.receive)]


# --- always visible ---------------------------------------------------------

def test_it_advertises_even_when_it_needs_nothing():
    """A peer who cannot find us cannot trade with us."""
    ads = of(decide(), actions.Advertise)
    assert len(ads) == 1 and ads[0].selling


def test_no_churn_when_the_listing_already_matches():
    first = listing(decide())
    mine = advertisement(
        advertisement_id="mine",
        station_id="P01",
        selling=first.selling,
        seeking=first.seeking,
    )
    assert not of(decide(advertisements=[mine]), actions.Advertise)


# --- offers to everyone, repeatedly -----------------------------------------

def test_it_sends_several_offers_to_the_same_peer():
    offers = of(decide(advertisements=[peer()]), actions.Offer)
    assert len(offers) == 2
    assert {o.recipient_id for o in offers} == {"P02"}


def test_offers_per_peer_are_capped_and_configurable():
    one_each = JesusPolicy(JesusWeights(max_offers_per_peer=1))
    built = build(advertisements=[peer()])
    assert len(of(one_each.decide(built), actions.Offer)) == 1


def test_existing_open_offers_count_against_the_per_peer_cap():
    mine = offer(
        offer_id="ours", proposer_id="P01", recipient_id="P02", give=(1, 0, 0), receive=(0, 1, 0)
    )
    offers = of(decide(advertisements=[peer()], offers=[mine]), actions.Offer)
    assert len(offers) == 1  # one of the two slots for this peer is already used


def test_it_offers_to_every_peer_it_can_see():
    offers = of(decide(advertisements=[peer("P03"), peer("P02")]), actions.Offer)
    assert {o.recipient_id for o in offers} == {"P02", "P03"}


def test_peers_are_considered_in_station_order():
    decided = decide(
        advertisements=[peer("P03"), peer("P02")],
        rules=rules(new_commands_per_station_per_tick=1, max_open_outgoing_offers=1),
    )
    assert [o.recipient_id for o in of(decided, actions.Offer)] == ["P02"]


def test_no_offer_to_a_peer_that_sells_nothing_we_want():
    decided = decide(advertisements=[peer(selling=(WATER,), seeking=(FOOD,))])
    assert not of(decided, actions.Offer)


# --- accepting nearly everything ---------------------------------------------
# The factory's specialty is water, which is priced as currency, so these pay
# in food to keep the arithmetic about the loss bar and nothing else.

def test_a_gift_is_accepted():
    decided = decide(offers=[offer(offer_id="gift-1", give=(0, 0, 1), receive=(0, 0, 0))])
    assert [a.offer_id for a in of(decided, actions.Accept)] == ["gift-1"]


def test_an_even_trade_is_accepted():
    """Scrooge would refuse this; Jesus wants the goods moving."""
    even = offer(offer_id="even", give=(0, 0, 1), receive=(0, 1, 0))
    assert of(decide(offers=[even]), actions.Accept)


def test_a_small_loss_is_accepted_as_the_cost_of_doing_business():
    slight = offer(offer_id="slight", give=(0, 0, 1), receive=(0, 2, 0))
    assert of(decide(offers=[slight]), actions.Accept)


def test_a_large_loss_is_still_refused():
    awful = offer(offer_id="awful", give=(0, 0, 1), receive=(0, 9, 0))
    assert not of(decide(offers=[awful]), actions.Accept)


def test_the_acceptable_loss_is_configurable():
    built = build(offers=[offer(offer_id="o", give=(0, 0, 1), receive=(0, 4, 0))])
    reckless = JesusPolicy(JesusWeights(acceptable_loss=10.0))
    assert of(reckless.decide(built), actions.Accept)
    assert not of(JesusPolicy().decide(built), actions.Accept)


def test_an_unaffordable_offer_is_refused():
    broke = observation(inventory=bundle(1, 1, 1))
    raid = offer(offer_id="x", give=(0, 0, 1), receive=(9, 0, 0))
    assert not of(decide(observation=broke, offers=[raid]), actions.Accept)


# --- the floor -----------------------------------------------------------------

#: Three water at one upkeep a tick is already near the floor, and components
#: are short enough that nine of them are worth having.
THIN = observation(inventory=bundle(3, 30, 5), upkeep_per_tick=bundle(1, 1, 1))


def test_it_will_not_trade_through_the_floor():
    raid = offer(offer_id="trap", give=(0, 0, 9), receive=(2, 0, 0))
    assert not of(decide(observation=THIN, offers=[raid]), actions.Accept)


def test_it_never_offers_what_would_take_it_through_the_floor():
    thin = observation(inventory=bundle(2, 10, 10), upkeep_per_tick=bundle(1, 1, 1))
    decided = offers_in(decide(observation=thin, advertisements=[peer()]))
    assert decided
    assert all(o.give[0] == 0 for o in decided)  # never pays in the resource at the floor


def test_the_floor_is_configurable():
    built = build(
        observation=THIN,
        offers=[offer(offer_id="trap", give=(0, 0, 9), receive=(2, 0, 0))],
    )
    daring = JesusPolicy(JesusWeights(floor_ticks=0.0))
    assert of(daring.decide(built), actions.Accept)


def test_a_resource_with_no_upkeep_never_blocks_a_trade():
    """Paying out the last component is fine when nothing consumes them."""
    no_upkeep = observation(inventory=bundle(30, 5, 1), upkeep_per_tick=bundle(1, 1, 0))
    spend = offer(offer_id="ok", give=(0, 2, 0), receive=(0, 0, 1))
    assert of(decide(observation=no_upkeep, offers=[spend]), actions.Accept)


# --- paying in what it makes ----------------------------------------------------
# The factory station's specialty is water.

def test_it_never_pays_for_its_own_specialty():
    """One-for-one clears the loss bar, and repeated it starves the station."""
    more_water = offer(offer_id="w", give=(3, 0, 0), receive=(0, 1, 0))
    assert not of(decide(offers=[more_water]), actions.Accept)


def test_it_never_offers_to_buy_its_own_specialty():
    decided = decide(advertisements=[peer(selling=(WATER, FOOD), seeking=(FOOD, COMPONENTS))])
    assert offers_in(decided)
    assert all(o.receive[0] == 0 for o in offers_in(decided))


def test_the_listing_sells_the_specialty_and_seeks_everything_else():
    ad = listing(decide())
    assert ad.selling == (WATER,)
    assert ad.seeking == (FOOD, COMPONENTS)


def test_it_pays_in_its_specialty_rather_than_anything_scarcer():
    """The peer would take components too, but water is what we make."""
    decided = decide(advertisements=[peer(selling=(FOOD,), seeking=(COMPONENTS, WATER))])
    assert [(o.give, o.receive) for o in offers_in(decided)] == [((6, 0, 0), (0, 2, 0))]


def test_specialty_offers_are_sized_by_config():
    generous = JesusPolicy(JesusWeights(specialty_offer_ratio=5, specialty_receive_qty=1))
    built = build(advertisements=[peer(selling=(FOOD,), seeking=(WATER,))])
    assert [(o.give, o.receive) for o in offers_in(generous.decide(built))] == [
        ((5, 0, 0), (0, 1, 0))
    ]


def test_a_peer_seeking_nothing_is_still_offered_the_specialty():
    decided = decide(advertisements=[peer(selling=(FOOD,), seeking=())])
    [made] = offers_in(decided)
    assert made.give == (6, 0, 0) and made.receive == (0, 2, 0)
    assert "unasked" in made.reason


def test_unsolicited_offers_can_be_turned_off():
    polite = JesusPolicy(JesusWeights(unsolicited_specialty=False))
    built = build(advertisements=[peer(selling=(FOOD,), seeking=())])
    assert offers_in(polite.decide(built)) == []


def test_it_will_pay_several_of_its_specialty_for_one_unit_it_lacks():
    """Five water for one food would have been refused as a loss before."""
    short_of_food = observation(inventory=bundle(35, 1, 30), upkeep_per_tick=bundle(1, 1, 1))
    dear = offer(offer_id="dear", give=(0, 1, 0), receive=(5, 0, 0))
    assert of(decide(observation=short_of_food, offers=[dear]), actions.Accept)


def test_a_specialty_with_nothing_left_is_bought_like_anything_else():
    """Once there is not even one unit of it on hand, it cannot be paid with,
    and the agent falls back to treating it like anything else it lacks."""
    empty = observation(inventory=bundle(0, 30, 30), upkeep_per_tick=bundle(1, 1, 1))
    refill = offer(offer_id="refill", give=(3, 0, 0), receive=(0, 0, 1))
    decided = decide(observation=empty, offers=[refill])
    assert of(decided, actions.Accept)
    assert WATER in listing(decided).seeking


def test_a_specialty_is_never_bought_back_as_long_as_any_of_it_remains():
    """Two water on hand is thin, but there is still some: the agent does not
    try to guess whether production will refill it, only whether the hold is
    actually empty yet.

    Before the fix, ``cover`` read a deliberately thin currency balance as a
    shortage and switched to buying it -- a live run sold its components
    down to near zero this way and then tried to buy them back.
    """
    thin = observation(inventory=bundle(2, 30, 30), upkeep_per_tick=bundle(1, 1, 1))
    refill = offer(offer_id="refill", give=(3, 0, 0), receive=(0, 0, 1))
    seller = peer(selling=(WATER, FOOD), seeking=(FOOD, COMPONENTS))
    decided = decide(observation=thin, offers=[refill], advertisements=[seller])
    assert not of(decided, actions.Accept)
    assert all(o.receive[0] == 0 for o in offers_in(decided))
    assert WATER not in listing(decided).seeking


def test_only_the_specialty_is_offered_unasked():
    """With nothing of the specialty left, a peer seeking nothing gets nothing."""
    empty = observation(inventory=bundle(0, 2, 30), upkeep_per_tick=bundle(1, 1, 1))
    decided = decide(observation=empty, advertisements=[peer(selling=(FOOD,), seeking=())])
    assert offers_in(decided) == []


def test_a_starving_station_offers_its_glut_to_a_peer_that_seeks_nothing():
    """Tick 90 of a live run: zero water, 279 components, and one listing up.

    P09 sold everything and sought nothing, so the agent of the day found no
    match and the station died nine ticks later still holding its components.
    """
    starving = observation(
        inventory=bundle(0, 8, 279),
        upkeep_per_tick=bundle(1, 1, 1),
        specialty=COMPONENTS,
    )
    p09 = peer("P09", selling=(WATER, FOOD, COMPONENTS), seeking=())
    decided = decide(observation=starving, advertisements=[p09])
    assert [(o.recipient_id, o.give, o.receive) for o in offers_in(decided)] == [
        ("P09", (0, 0, 6), (2, 0, 0)),  # water first: it is the one at zero
        ("P09", (0, 0, 6), (0, 2, 0)),
    ]


def test_it_keeps_buying_what_drains_before_the_short_horizon_notices():
    """Tick 40 of the same run: food at seven ticks of cover, three sellers."""
    draining = observation(
        inventory=bundle(2, 7, 185),
        upkeep_per_tick=bundle(1, 1, 1),
        specialty=COMPONENTS,
    )
    food_seller = peer("P02", selling=(FOOD,), seeking=(WATER, COMPONENTS))
    decided = decide(observation=draining, advertisements=[food_seller])
    assert [(o.give, o.receive) for o in offers_in(decided)] == [((0, 0, 6), (0, 2, 0))]


# --- not hoarding ----------------------------------------------------------------
# Tick 88 of a live run: 90 water, 34 food, components spent as fast as they
# were made, and 32 ticks to go.  With the margin both lines cap at 35 ticks, so
# enough is 20 ticks of cover and 55 water is surplus.

def glut(water, components=6):
    return observation(
        inventory=bundle(water, 34, components),
        upkeep_per_tick=bundle(1, 1, 1),
        specialty=COMPONENTS,
    )


GLUT = glut(90)
ENDGAME = rules(new_commands_per_station_per_tick=6, max_open_outgoing_offers=6, duration_ticks=32)


def endgame(**overrides):
    overrides.setdefault("observation", GLUT)
    overrides.setdefault("rules", ENDGAME)
    return decide(**overrides)


def ours(offer_id, recipient, give, receive=(0, 0, 0)):
    return offer(
        offer_id=offer_id, proposer_id="P01", recipient_id=recipient, give=give, receive=receive
    )


def test_it_stops_buying_what_it_already_has_plenty_of():
    """It used to offer six components for two water to every seller, every tick."""
    seller = peer("P04", selling=(WATER, FOOD), seeking=())
    decided = endgame(advertisements=[seller])
    assert offers_in(decided) == []
    assert listing(decided).seeking == ()


def test_the_lines_are_configurable():
    greedy = JesusPolicy(JesusWeights(enough_ticks=100.0, keep_ticks=200.0))
    built = build(
        observation=glut(90, components=30),
        advertisements=[peer("P04", selling=(WATER,), seeking=())],
    )
    assert [o.receive for o in offers_in(greedy.decide(built))] == [(2, 0, 0)]


def test_surplus_is_given_to_a_peer_that_seeks_it():
    decided = endgame(advertisements=[peer("P06", selling=(FOOD,), seeking=(WATER,))])
    assert [(g.recipient_id, g.give) for g in gifts_in(decided)] == [("P06", (5, 0, 0))]


def test_nothing_is_given_to_a_peer_that_seeks_nothing():
    decided = endgame(advertisements=[peer("P09", selling=(WATER, FOOD), seeking=())])
    assert gifts_in(decided) == []


def test_surplus_is_shared_between_the_peers_that_seek_it():
    seekers = [peer(station, selling=(), seeking=(WATER,)) for station in ("P03", "P02")]
    decided = endgame(advertisements=seekers)
    assert [(g.recipient_id, g.give) for g in gifts_in(decided)] == [
        ("P02", (5, 0, 0)),
        ("P03", (5, 0, 0)),
    ]


def test_gifts_never_take_a_resource_below_the_keep_line():
    """Thirty-eight water against a 35-tick line leaves three to give."""
    seekers = [peer(station, selling=(), seeking=(WATER,)) for station in ("P02", "P03")]
    decided = endgame(observation=glut(38), advertisements=seekers)
    assert [(g.recipient_id, g.give) for g in gifts_in(decided)] == [("P02", (3, 0, 0))]


def test_open_offers_count_against_what_can_be_given():
    """Offers reserve nothing, so whatever they give may already be gone."""
    promised = ours("big", "P05", give=(52, 0, 0))
    decided = endgame(
        offers=[promised], advertisements=[peer("P06", selling=(), seeking=(WATER,))]
    )
    assert [g.give for g in gifts_in(decided)] == [(3, 0, 0)]


def test_surplus_a_peer_takes_is_not_also_given_away():
    asking = offer(offer_id="ask", proposer_id="P06", give=(0, 0, 1), receive=(3, 0, 0))
    decided = endgame(
        observation=glut(38),
        offers=[asking],
        advertisements=[peer("P07", selling=(), seeking=(WATER,))],
    )
    assert [a.offer_id for a in of(decided, actions.Accept)] == ["ask"]
    assert gifts_in(decided) == []


def test_a_gift_of_something_it_has_plenty_of_is_declined():
    """The giver's surplus should reach a station that needs it."""
    more_water = offer(offer_id="w", proposer_id="P04", give=(2, 0, 0), receive=(0, 0, 0))
    assert not of(endgame(offers=[more_water]), actions.Accept)


def test_a_peer_asking_only_for_surplus_gets_it_at_any_price():
    """One component for ten water is a loss on paper, but the water is spare."""
    cheap = offer(offer_id="cheap", proposer_id="P06", give=(0, 0, 1), receive=(10, 0, 0))
    assert of(endgame(offers=[cheap]), actions.Accept)


def test_a_peer_asking_for_more_than_the_surplus_is_judged_as_a_trade():
    greedy = offer(offer_id="greedy", proposer_id="P06", give=(0, 0, 1), receive=(60, 0, 0))
    assert not of(endgame(offers=[greedy]), actions.Accept)


def test_its_own_gifts_are_not_withdrawn_as_losses():
    gift = ours("gift", "P06", give=(5, 0, 0))
    assert not of(endgame(offers=[gift]), actions.Withdraw)


def test_a_gift_it_can_no_longer_spare_is_withdrawn():
    gift = ours("gift", "P06", give=(5, 0, 0))
    decided = endgame(observation=glut(37), offers=[gift])
    assert [a.object_id for a in of(decided, actions.Withdraw)] == ["gift"]


def test_near_the_end_of_the_run_everything_it_will_not_burn_is_given():
    """Ten ticks out, the line is 13 ticks; mid-run it would be 40."""
    whole = JesusPolicy(JesusWeights(gift_lot=100))
    asker = [peer("P06", selling=(), seeking=(WATER, FOOD))]
    late = build(observation=GLUT, advertisements=asker, rules=rules(duration_ticks=10))
    early = build(observation=GLUT, advertisements=asker)
    assert [g.give for g in gifts_in(whole.decide(late))] == [(77, 0, 0), (0, 21, 0)]
    assert [g.give for g in gifts_in(whole.decide(early))] == [(50, 0, 0)]


def test_the_listing_sells_its_surplus():
    assert listing(endgame()).selling == (WATER, COMPONENTS)


# --- budget and ranking --------------------------------------------------------

def test_it_spends_the_whole_command_budget():
    decided = decide(
        advertisements=[peer("P02"), peer("P03")],
        rules=rules(new_commands_per_station_per_tick=3),
    )
    assert len(decided) == 3


def test_offers_outrank_listings():
    decided = decide(advertisements=[peer()], rules=rules(new_commands_per_station_per_tick=1))
    assert isinstance(decided[0], actions.Offer)


def test_it_trades_far_more_than_scrooge_on_the_same_state():
    """The two agents' theses, side by side on one comfortable position."""
    from bazaar.brain.policy.scrooge import ScroogePolicy

    built = state(advertisements=[peer()], rules=BUSY)
    assert len(JesusPolicy().decide(built)) > len(ScroogePolicy().decide(built))
