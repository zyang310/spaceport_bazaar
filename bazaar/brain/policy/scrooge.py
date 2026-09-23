"""Scrooge: reserves first, charity never.

Where the utility agent asks "is this trade worth something?", Scrooge asks
"can I afford to care yet?".  It measures everything in **ticks of cover** --
how many more ticks the stock would last at the current upkeep -- and treats
that number as the thing to defend.

Three rules follow from that, and between them they are the policy:

1. **It never gives anything away.**  No offer it proposes has an empty
   ``receive``, and no advertisement it posts has an empty ``seeking``.  An
   advertisement that only sells is an invitation to be asked for a favour, so
   it is not posted.  Gifts arriving the other way are accepted gladly.
2. **It only goes shopping when something is running out.**  Above
   ``danger_ticks`` of cover it posts nothing -- no offers, no advertisements --
   and merely reads its own position.  Below it, the shortage sets the agenda.
3. **It pays in its specialty.**  That is the resource it produces, so parting
   with it costs least.  Before anything is in danger it will only accept a
   trade priced in that resource, and when it does go shopping it reaches for
   it first.

Everything is a pure function of the state handed in, so a captured state
replays identically.  Tunables live in ``bazaar/config.py``.
"""

import math

from ... import actions, config
from ...validation import model
from .. import store

#: Bundle field order, which is also the order tuples are written in.
_BUNDLE_ORDER = (
    model.Resource.RESOURCE_WATER,
    model.Resource.RESOURCE_FOOD,
    model.Resource.RESOURCE_COMPONENTS,
)


class ScroogePolicy:
    """Defends its reserves, pays in its specialty, and gives nothing away."""

    name = "scrooge"

    def __init__(self, weights: config.ScroogeWeights = config.DEFAULT_SCROOGE):
        self.weights = weights

    # --- projection ------------------------------------------------------
    def _order(self, state: model.State) -> list[model.Resource]:
        """Resource order from the rules, which also breaks ties."""
        return list(state.rules.resource_order) or list(model.Resource)

    def horizon(self, state: model.State) -> int:
        """How far ahead to look, never past the end of the run.

        Projecting beyond the final tick would have the agent hoard for ticks
        that never arrive, which is not thrift but paralysis.
        """
        horizon = self.weights.horizon
        if not self.weights.cap_horizon_to_run:
            return horizon
        remaining = state.rules.duration_ticks - state.tick
        return max(1, min(horizon, remaining))

    def cover(self, state: model.State) -> dict[model.Resource, float]:
        """Ticks of cover per resource: stock divided by upkeep.

        A resource with no upkeep is never consumed, so it lasts forever and
        can never be in danger.
        """
        observation = state.observation
        ticks = {}
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            if upkeep <= 0:
                ticks[resource] = math.inf
            else:
                ticks[resource] = observation.inventory.get(resource) / upkeep
        return ticks

    def critical(self, state, cover) -> tuple[model.Resource, ...]:
        """Resources whose cover has fallen below the danger line."""
        return tuple(r for r in self._order(state) if cover[r] < self.weights.danger_ticks)

    def needs(self, state: model.State) -> dict[model.Resource, int]:
        """How short we are of each resource over the horizon.

        Positive is a deficit; negative means that much surplus.
        """
        observation = state.observation
        horizon = self.horizon(state)
        return {
            resource: observation.upkeep_per_tick.get(resource) * horizon
            + self.weights.buffer
            - observation.inventory.get(resource)
            for resource in self._order(state)
        }

    def prices(self, state, needs, cover) -> dict[model.Resource, float]:
        """What a unit of each resource is worth to us right now.

        The specialty is discounted, because we make more of it every tick --
        unless it is the thing running out, in which case it is as dear as
        anything else.
        """
        weights = self.weights
        specialty = state.observation.specialty
        priced = {}
        for resource, need in needs.items():
            if cover[resource] < weights.danger_ticks:
                price = weights.price_critical
            elif need > 0:
                price = weights.price_deficit
            elif need < 0:
                price = weights.price_surplus
            else:
                price = weights.price_neutral
            if resource == specialty and cover[resource] >= weights.danger_ticks:
                price *= weights.specialty_discount
            priced[resource] = price
        return priced

    # --- valuation -------------------------------------------------------
    def value(self, prices, bundle: model.Bundle) -> float:
        """What a bundle is worth to us, in our own prices."""
        return sum(prices[resource] * bundle.get(resource) for resource in prices)

    def _affordable(self, state, paid: model.Bundle) -> bool:
        inventory = state.observation.inventory
        return all(inventory.get(r) >= paid.get(r) for r in self._order(state))

    def _breaches_reserve(self, state, cover, received, paid) -> bool:
        """Would this trade push something below the danger line?

        Only a trade that makes matters worse is refused: a resource already
        under the line is no reason to turn down help with it.
        """
        observation = state.observation
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            if upkeep <= 0:
                continue
            stock = observation.inventory.get(resource) - paid.get(resource) + received.get(resource)
            after = stock / upkeep
            if after < self.weights.danger_ticks and after < cover[resource]:
                return True
        return False

    def _pays_only_specialty(self, state, paid: model.Bundle) -> bool:
        """Is our side of this trade priced purely in the thing we produce?"""
        specialty = state.observation.specialty
        return all(paid.get(r) == 0 for r in self._order(state) if r != specialty)

    def _expiry(self, state, ttl_ticks: int, ceiling_rule: str) -> int:
        ceiling = getattr(state.rules, ceiling_rule)
        return state.tick + max(1, min(ttl_ticks, ceiling))

    # --- candidates ------------------------------------------------------
    def _accepts(self, state, cover, prices, in_danger: bool) -> list:
        """Incoming offers worth taking.

        A gift costs nothing and is always taken.  Anything with a price is
        judged against how badly we need what is on the table: while nothing is
        in danger the agent is deliberately hard to please, and will only part
        with its own specialty.
        """
        weights = self.weights
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            received, paid = offer.give, offer.receive
            gain = self.value(prices, received)
            cost = self.value(prices, paid)

            if paid.is_zero():
                # Free is free. Scrooge does not give, but he certainly takes.
                chosen.append(
                    actions.Accept(
                        offer_id=offer.offer_id,
                        reason=f"free {received.as_tuple()} from {offer.proposer_id}",
                        score=gain + weights.price_critical,
                    )
                )
                continue

            if not self._affordable(state, paid):
                continue
            if self._breaches_reserve(state, cover, received, paid):
                continue

            if in_danger:
                if gain <= cost:
                    continue
                reason = f"gain {received.as_tuple()} for {paid.as_tuple()} while short"
            else:
                # Nothing is running out, so the bar is high and the currency
                # is fixed: our own resource, at a clear profit.
                if not self._pays_only_specialty(state, paid):
                    continue
                if gain < cost * weights.advantage_ratio:
                    continue
                reason = (
                    f"{gain / cost:.1f}x return paying only "
                    f"{state.observation.specialty.name.removeprefix('RESOURCE_')}"
                )
            chosen.append(
                actions.Accept(offer_id=offer.offer_id, reason=reason, score=gain - cost)
            )
        return chosen

    def _payment_options(self, state, cover, critical) -> tuple[model.Resource, ...]:
        """What we are willing to part with, best first.

        The specialty leads, because we make more of it every tick, so giving
        it up costs least.  Anything else genuinely spare follows in resource
        order.  This is a preference rather than a single choice: a peer that
        will not take our specialty may still take something else we can
        afford, and refusing on principle would just leave us short.
        """
        specialty = state.observation.specialty
        options = []
        if specialty not in critical and cover[specialty] >= self.weights.danger_ticks:
            options.append(specialty)
        for resource in self._order(state):
            if resource in options or resource in critical:
                continue
            if cover[resource] > self.weights.danger_ticks:
                options.append(resource)
        return tuple(options)

    def _offers(self, state, cover, prices, critical) -> list:
        """Go shopping, but only for what is running out.

        Called only when something is in danger.  One offer per peer, paid in
        the specialty wherever possible, and never for free.
        """
        weights = self.weights
        options = self._payment_options(state, cover, critical)
        if not options:
            return []  # nothing spare to trade with
        already = {offer.recipient_id for offer in store.my_open_offers(state)}
        chosen = []
        for advertisement in store.peer_active_advertisements(state):
            if advertisement.station_id in already:
                continue  # one open offer per peer at a time
            wanted = [r for r in critical if r in advertisement.selling]
            if not wanted:
                continue
            # Pay in the most preferred coin this peer will actually accept.
            payment = next((p for p in options if p in advertisement.seeking), None)
            if payment is None:
                continue  # they do not want anything we are willing to part with
            want = wanted[0]
            receive = model.Bundle.of(
                tuple(weights.offer_receive_qty if r == want else 0 for r in _BUNDLE_ORDER)
            )
            give = model.Bundle.of(
                tuple(
                    weights.offer_ratio * weights.offer_receive_qty if r == payment else 0
                    for r in _BUNDLE_ORDER
                )
            )
            if give.is_zero():
                continue  # never propose a gift
            if not self._affordable(state, give):
                continue
            if self._breaches_reserve(state, cover, receive, give):
                continue
            gain = self.value(prices, receive)
            cost = self.value(prices, give)
            if gain <= cost:
                continue
            chosen.append(
                actions.Offer(
                    recipient_id=advertisement.station_id,
                    give=give.as_tuple(),
                    receive=receive.as_tuple(),
                    expires_tick=self._expiry(state, weights.offer_ttl_ticks, "max_offer_ttl_ticks"),
                    reason=(
                        f"{advertisement.station_id} sells "
                        f"{want.name.removeprefix('RESOURCE_')} we are short of, "
                        f"paid in {payment.name.removeprefix('RESOURCE_')}"
                    ),
                    score=gain - cost,
                )
            )
        return chosen

    def _advertise(self, state, cover, critical) -> list:
        """Post a listing only when there is something we want to buy.

        ``seeking`` is never empty: an advertisement that only sells is an
        advertisement for charity, and this agent does not run one.
        """
        # List everything we would part with, so a peer can pick: more ways to
        # be offered what we need, and the specialty still leads the list.
        selling = self._payment_options(state, cover, critical)
        seeking = tuple(critical)
        if not seeking:
            return []
        current = store.my_active_advertisement(state)
        if current is not None and (tuple(current.selling), tuple(current.seeking)) == (
            selling,
            seeking,
        ):
            return []  # no churn: the listing already says this
        return [
            actions.Advertise(
                selling=selling,
                seeking=seeking,
                expires_tick=self._expiry(
                    state, self.weights.advertisement_ttl_ticks, "max_publication_ttl_ticks"
                ),
                reason="short of "
                + "+".join(r.name.removeprefix("RESOURCE_") for r in seeking),
                score=self.weights.advertise_score,
            )
        ]

    def _withdrawals(self, state, prices, critical) -> list:
        """Take back anything that no longer serves us.

        That includes our own listing once the shortage that justified it has
        passed: leaving it up only invites offers we have already decided we do
        not want.
        """
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: o.offer_id):
            gain = self.value(prices, offer.receive)
            cost = self.value(prices, offer.give)
            if gain <= cost:
                chosen.append(
                    actions.Withdraw(
                        object_id=offer.offer_id,
                        reason=f"our offer now costs {cost:.1f} to gain {gain:.1f}",
                        score=self.weights.withdraw_score,
                    )
                )
        current = store.my_active_advertisement(state)
        if current is not None and (not critical or not current.seeking):
            chosen.append(
                actions.Withdraw(
                    object_id=current.advertisement_id,
                    reason="nothing we still need"
                    if not critical
                    else "listing asks for nothing in return",
                    score=self.weights.withdraw_score,
                )
            )
        return chosen

    # --- the decision ----------------------------------------------------
    def decide(self, state: model.State) -> list:
        """Pure function of the state: same state in, same actions out."""
        if state.phase is not model.Phase.PHASE_RUNNING:
            return []
        if state.observation.health <= 0:
            return []  # a failed station is permanent; do not trade from it

        cover = self.cover(state)
        critical = self.critical(state, cover)
        in_danger = bool(critical)
        needs = self.needs(state)
        prices = self.prices(state, needs, cover)

        # Generated in resource order, then station order, so that the stable
        # sort below leaves equal scores in exactly that order.
        candidates = [*self._accepts(state, cover, prices, in_danger)]
        if in_danger:
            # Only a shortage justifies going out and asking for something.
            candidates += self._advertise(state, cover, critical)
            candidates += self._offers(state, cover, prices, critical)
        candidates += self._withdrawals(state, prices, critical)

        ranked = sorted(_without_charity(candidates), key=lambda action: -action.score)
        return self._within_limits(state, ranked)

    def _within_limits(self, state, ranked: list) -> list:
        """Obey the run's command limits, best-scoring first.

        Every command stores a result and the run caps how many it keeps, so
        capacity is the hard ceiling: once it is gone nothing is sent.
        """
        budget = min(
            state.rules.new_commands_per_station_per_tick,
            store.remaining_result_capacity(state),
        )
        offer_slots = store.remaining_offer_slots(state)
        chosen = []
        for action in ranked:
            if len(chosen) >= budget:
                break
            if isinstance(action, actions.Offer):
                if offer_slots <= 0:
                    continue
                offer_slots -= 1
            chosen.append(action)
        return chosen


def _without_charity(candidates: list) -> list:
    """Drop anything that would amount to giving something away.

    The generators already refuse to build these, so this is a backstop rather
    than a filter that does real work -- but it is the one rule the policy is
    named for, and it should be impossible to violate by accident.
    """
    kept = []
    for action in candidates:
        if isinstance(action, actions.Offer) and model.Bundle.of(action.receive).is_zero():
            continue
        if isinstance(action, actions.Advertise) and not action.seeking:
            continue
        kept.append(action)
    return kept
