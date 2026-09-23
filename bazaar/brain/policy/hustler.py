"""Hustler: volume over margin.

The bet here is the opposite of Scrooge's.  Sitting on a full warehouse earns
nothing; every settled trade moves resources toward whoever values them more,
and some of that value lands on us.  So the Hustler is always listed, offers to
everyone who will look at it, and takes any deal that is not actually bad.

What that means concretely:

1. **It always has a listing up.**  Not only when short -- a listing is how
   peers find you, and a peer who cannot find you cannot trade with you.
2. **It offers to everyone, more than once.**  Other agents allow one open
   offer per peer; this one fills every slot the rules permit, pairing anything
   a peer sells against anything they seek.
3. **It accepts almost everything.**  A trade only has to clear
   ``-acceptable_loss``, because a small loss that keeps goods moving beats a
   perfect trade that never happens.
4. **It will not trade itself to death.**  ``floor_ticks`` of cover is the one
   line it holds, and nothing crosses it.

The short horizon is deliberate: the Hustler trades on what is in front of it,
not on what it might want ten ticks from now.  Tunables are in
``bazaar/config.py``.
"""

import math

from ... import actions, config
from ...validation import model
from .. import store

_BUNDLE_ORDER = (
    model.Resource.RESOURCE_WATER,
    model.Resource.RESOURCE_FOOD,
    model.Resource.RESOURCE_COMPONENTS,
)


class HustlerPolicy:
    """Always listed, always offering, and hard to say no to."""

    name = "hustler"

    def __init__(self, weights: config.HustlerWeights = config.DEFAULT_HUSTLER):
        self.weights = weights

    # --- projection ------------------------------------------------------
    def _order(self, state):
        return list(state.rules.resource_order) or list(model.Resource)

    def cover(self, state) -> dict:
        """Ticks of cover per resource; a resource with no upkeep lasts forever."""
        observation = state.observation
        ticks = {}
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            ticks[resource] = (
                math.inf if upkeep <= 0 else observation.inventory.get(resource) / upkeep
            )
        return ticks

    def needs(self, state) -> dict:
        observation = state.observation
        weights = self.weights
        return {
            resource: observation.upkeep_per_tick.get(resource) * weights.horizon
            + weights.buffer
            - observation.inventory.get(resource)
            for resource in self._order(state)
        }

    def prices(self, needs) -> dict:
        weights = self.weights
        return {
            resource: (
                weights.price_deficit
                if need > 0
                else weights.price_surplus
                if need < 0
                else weights.price_neutral
            )
            for resource, need in needs.items()
        }

    # --- valuation -------------------------------------------------------
    def value(self, prices, bundle: model.Bundle) -> float:
        return sum(prices[resource] * bundle.get(resource) for resource in prices)

    def _affordable(self, state, paid: model.Bundle) -> bool:
        inventory = state.observation.inventory
        return all(inventory.get(r) >= paid.get(r) for r in self._order(state))

    def _breaches_floor(self, state, cover, received, paid) -> bool:
        """The one rule: never fall through the floor on anything.

        A resource already below it is not a reason to refuse help with it, so
        only a trade that makes matters worse is blocked.
        """
        observation = state.observation
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            if upkeep <= 0:
                continue
            stock = observation.inventory.get(resource) - paid.get(resource) + received.get(resource)
            after = stock / upkeep
            if after < self.weights.floor_ticks and after < cover[resource]:
                return True
        return False

    def _expiry(self, state, ttl_ticks, ceiling_rule) -> int:
        ceiling = getattr(state.rules, ceiling_rule)
        return state.tick + max(1, min(ttl_ticks, ceiling))

    def _spare(self, state, cover) -> tuple:
        """Everything we could pay with without going through the floor."""
        return tuple(
            r
            for r in self._order(state)
            if cover[r] > self.weights.floor_ticks
            and state.observation.inventory.get(r) >= self.weights.offer_ratio
        )

    def _wanted(self, state, needs) -> tuple:
        """What we would take. Anything not in clear surplus counts."""
        return tuple(r for r in self._order(state) if needs[r] >= 0) or tuple(self._order(state))

    # --- candidates ------------------------------------------------------
    def _accepts(self, state, cover, prices) -> list:
        """Take nearly everything: the bar is a small loss, not a profit."""
        weights = self.weights
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            received, paid = offer.give, offer.receive
            worth = self.value(prices, received) - self.value(prices, paid)
            if paid.is_zero():
                chosen.append(
                    actions.Accept(
                        offer_id=offer.offer_id,
                        reason=f"free {received.as_tuple()} from {offer.proposer_id}",
                        score=worth + weights.price_deficit,
                    )
                )
                continue
            if not self._affordable(state, paid):
                continue
            if self._breaches_floor(state, cover, received, paid):
                continue
            if worth < -weights.acceptable_loss:
                continue
            chosen.append(
                actions.Accept(
                    offer_id=offer.offer_id,
                    reason=f"{received.as_tuple()} for {paid.as_tuple()}, worth {worth:+.1f}",
                    score=worth,
                )
            )
        return chosen

    def _offers(self, state, cover, prices, needs) -> list:
        """Offer to everyone, pairing whatever they sell against whatever they want.

        Several offers may go to the same peer: the point is to have as many
        live proposals out as the rules allow.
        """
        weights = self.weights
        spare = self._spare(state, cover)
        wanted = self._wanted(state, needs)
        open_per_peer = {}
        for mine in store.my_open_offers(state):
            open_per_peer[mine.recipient_id] = open_per_peer.get(mine.recipient_id, 0) + 1

        chosen = []
        for advertisement in store.peer_active_advertisements(state):
            room = weights.max_offers_per_peer - open_per_peer.get(advertisement.station_id, 0)
            if room <= 0:
                continue
            made = 0
            for want in self._order(state):
                if made >= room:
                    break
                if want not in advertisement.selling or want not in wanted:
                    continue
                for pay in self._order(state):
                    if made >= room:
                        break
                    if pay == want or pay not in advertisement.seeking or pay not in spare:
                        continue
                    receive = model.Bundle.of(
                        tuple(weights.offer_receive_qty if r == want else 0 for r in _BUNDLE_ORDER)
                    )
                    give = model.Bundle.of(
                        tuple(weights.offer_ratio if r == pay else 0 for r in _BUNDLE_ORDER)
                    )
                    if give.is_zero() or not self._affordable(state, give):
                        continue
                    if self._breaches_floor(state, cover, receive, give):
                        continue
                    worth = self.value(prices, receive) - self.value(prices, give)
                    if worth < -weights.acceptable_loss:
                        continue
                    made += 1
                    chosen.append(
                        actions.Offer(
                            recipient_id=advertisement.station_id,
                            give=give.as_tuple(),
                            receive=receive.as_tuple(),
                            expires_tick=self._expiry(
                                state, weights.offer_ttl_ticks, "max_offer_ttl_ticks"
                            ),
                            reason=(
                                f"{advertisement.station_id} sells "
                                f"{want.name.removeprefix('RESOURCE_')} for "
                                f"{pay.name.removeprefix('RESOURCE_')}"
                            ),
                            score=worth + weights.offer_bonus,
                        )
                    )
        return chosen

    def _advertise(self, state, cover, needs) -> list:
        """Keep a listing up at all times; a peer who cannot find us cannot trade."""
        selling = self._spare(state, cover)
        seeking = self._wanted(state, needs)
        if not selling and not seeking:
            return []
        current = store.my_active_advertisement(state)
        if current is not None and (tuple(current.selling), tuple(current.seeking)) == (
            selling,
            seeking,
        ):
            return []
        return [
            actions.Advertise(
                selling=selling,
                seeking=seeking,
                expires_tick=self._expiry(
                    state, self.weights.advertisement_ttl_ticks, "max_publication_ttl_ticks"
                ),
                reason="stay visible" if current is None else "refresh the listing",
                score=self.weights.advertise_score,
            )
        ]

    def _withdrawals(self, state, prices) -> list:
        """Only pull an offer that has become genuinely bad, and reluctantly."""
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: o.offer_id):
            worth = self.value(prices, offer.receive) - self.value(prices, offer.give)
            if worth < -self.weights.acceptable_loss:
                chosen.append(
                    actions.Withdraw(
                        object_id=offer.offer_id,
                        reason=f"our offer is now worth {worth:+.1f}",
                        score=self.weights.withdraw_score,
                    )
                )
        return chosen

    # --- the decision ----------------------------------------------------
    def decide(self, state) -> list:
        """Pure function of the state: same state in, same actions out."""
        if state.phase is not model.Phase.PHASE_RUNNING:
            return []
        if state.observation.health <= 0:
            return []

        cover = self.cover(state)
        needs = self.needs(state)
        prices = self.prices(needs)

        candidates = [
            *self._accepts(state, cover, prices),
            *self._offers(state, cover, prices, needs),
            *self._advertise(state, cover, needs),
            *self._withdrawals(state, prices),
        ]
        ranked = sorted(candidates, key=lambda action: -action.score)
        return self._within_limits(state, ranked)

    def _within_limits(self, state, ranked) -> list:
        """Use the whole budget, every tick, up to what the rules allow."""
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
