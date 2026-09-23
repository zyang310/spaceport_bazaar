"""The decision: score what we could do, then do the best of it.

The agent projects how much of each resource it will need over a short horizon,
prices each resource by whether it is short or spare, and then values every
candidate action in those prices.  Nothing here is clever; it is meant to be
readable and deterministic, so that the same state always produces the same
actions and a captured run can be replayed in a test.

All tunables live in ``bazaar/config.py``.  Note that the practice exercise
stays at tick 0, so upkeep never actually runs there and these projections only
start to matter in a real game.
"""

from ... import actions, config
from ...validation import model
from .. import store


class UtilityPolicy:
    """Scores candidate actions against projected need and picks the best."""

    name = "utility"

    def __init__(self, weights: config.PolicyWeights = config.DEFAULT_WEIGHTS):
        self.weights = weights

    # --- projection ------------------------------------------------------
    def _order(self, state: model.State) -> list[model.Resource]:
        """Resource order from the rules, which also breaks ties."""
        return list(state.rules.resource_order) or list(model.Resource)

    def needs(self, state: model.State) -> dict[model.Resource, int]:
        """How short we are of each resource over the horizon.

        Positive is a deficit; negative means that much surplus.
        """
        observation = state.observation
        weights = self.weights
        projected = {}
        for resource in self._order(state):
            target = observation.upkeep_per_tick.get(resource) * weights.horizon + weights.buffer
            projected[resource] = target - observation.inventory.get(resource)
        return projected

    def prices(self, needs: dict) -> dict[model.Resource, float]:
        """What a unit of each resource is worth to us right now."""
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

    def _surplus_and_deficit(self, state, needs):
        """Split resources into what we would sell and what we want.

        The specialty always counts as sellable surplus, because the station
        produces it.  It is therefore never listed as sought, so we cannot
        advertise wanting and selling the same thing.
        """
        order = self._order(state)
        specialty = state.observation.specialty
        surplus = [r for r in order if needs[r] < 0 or r == specialty]
        deficit = [r for r in order if needs[r] > 0 and r != specialty]
        return tuple(surplus), tuple(deficit)

    # --- valuation -------------------------------------------------------
    def value(self, prices, received: model.Bundle, paid: model.Bundle) -> float:
        """What a trade is worth from our side, in our own prices."""
        return sum(
            prices[resource] * (received.get(resource) - paid.get(resource)) for resource in prices
        )

    def _affordable(self, state, paid: model.Bundle) -> bool:
        inventory = state.observation.inventory
        return all(inventory.get(r) >= paid.get(r) for r in self._order(state))

    def _creates_deficit(self, needs, received: model.Bundle, paid: model.Bundle) -> bool:
        """Would this trade push a resource we are fine on into shortage?

        ``need`` is ``target - inventory``, so paying raises it and receiving
        lowers it.  A resource already in deficit is not a reason to refuse a
        trade that does not make it worse.
        """
        for resource, need in needs.items():
            after = need + paid.get(resource) - received.get(resource)
            if after > 0 and need <= 0:
                return True
        return False

    def _expiry(self, state, ttl_ticks: int, ceiling_rule: str) -> int:
        """An expiry that is in the future and inside the run's ceiling."""
        ceiling = getattr(state.rules, ceiling_rule)
        return state.tick + max(1, min(ttl_ticks, ceiling))

    # --- candidates ------------------------------------------------------
    def _accepts(self, state, needs, prices) -> list:
        """Incoming offers worth taking.  Gifts always are."""
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            received, paid = offer.give, offer.receive
            worth = self.value(prices, received, paid)
            if paid.is_zero():
                chosen.append(
                    actions.Accept(
                        offer_id=offer.offer_id,
                        reason=f"free gift of {received.as_tuple()} from {offer.proposer_id}",
                        score=max(worth, 0.0) + 1.0,
                    )
                )
                continue
            if worth <= 0:
                continue
            if not self._affordable(state, paid) or self._creates_deficit(needs, received, paid):
                continue
            chosen.append(
                actions.Accept(
                    offer_id=offer.offer_id,
                    reason=f"gain {received.as_tuple()} for {paid.as_tuple()}, worth {worth:.1f}",
                    score=worth,
                )
            )
        return chosen

    def _advertise(self, state, surplus, deficit) -> list:
        """Publish our position, but only when it has actually changed."""
        if not surplus and not deficit:
            return []
        current = store.my_active_advertisement(state)
        if current is not None and (tuple(current.selling), tuple(current.seeking)) == (
            surplus,
            deficit,
        ):
            return []  # no churn: the listing already says this
        return [
            actions.Advertise(
                selling=surplus,
                seeking=deficit,
                expires_tick=self._expiry(
                    state, self.weights.advertisement_ttl_ticks, "max_publication_ttl_ticks"
                ),
                reason="publish current surplus and needs"
                if current is None
                else "position changed since the active listing",
                score=self.weights.advertise_score,
            )
        ]

    def _offers(self, state, needs, prices, surplus, deficit) -> list:
        """Propose trades to peers whose listing matches what we want."""
        weights = self.weights
        already = {offer.recipient_id for offer in store.my_open_offers(state)}
        order = self._order(state)
        chosen = []
        for advertisement in store.peer_active_advertisements(state):
            if advertisement.station_id in already:
                continue  # one open offer per peer at a time
            wanted = [r for r in order if r in advertisement.selling and r in deficit]
            payable = [r for r in order if r in advertisement.seeking and r in surplus]
            if not wanted or not payable:
                continue
            want, pay = wanted[0], payable[0]
            receive = model.Bundle.of(
                tuple(weights.offer_receive_qty if r == want else 0 for r in _BUNDLE_ORDER)
            )
            give = model.Bundle.of(
                tuple(
                    weights.offer_ratio * weights.offer_receive_qty if r == pay else 0
                    for r in _BUNDLE_ORDER
                )
            )
            worth = self.value(prices, receive, give)
            if worth <= 0:
                continue
            if not self._affordable(state, give) or self._creates_deficit(needs, receive, give):
                continue
            chosen.append(
                actions.Offer(
                    recipient_id=advertisement.station_id,
                    give=give.as_tuple(),
                    receive=receive.as_tuple(),
                    expires_tick=self._expiry(state, weights.offer_ttl_ticks, "max_offer_ttl_ticks"),
                    reason=(
                        f"{advertisement.station_id} sells {want.name.removeprefix('RESOURCE_')} "
                        f"and seeks {pay.name.removeprefix('RESOURCE_')}"
                    ),
                    score=worth,
                )
            )
        return chosen

    def _withdrawals(self, state, prices, surplus, deficit) -> list:
        """Drop our own offers that have gone sour, and a listing saying nothing."""
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: o.offer_id):
            worth = self.value(prices, offer.receive, offer.give)
            if worth < 0:
                chosen.append(
                    actions.Withdraw(
                        object_id=offer.offer_id,
                        reason=f"our offer is now worth {worth:.1f} to us",
                        score=self.weights.withdraw_score,
                    )
                )
        if not surplus and not deficit:
            current = store.my_active_advertisement(state)
            if current is not None:
                chosen.append(
                    actions.Withdraw(
                        object_id=current.advertisement_id,
                        reason="nothing left to sell or seek",
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

        needs = self.needs(state)
        prices = self.prices(needs)
        surplus, deficit = self._surplus_and_deficit(state, needs)

        # Generated in resource order, then station order, so that the stable
        # sort below leaves equal scores in exactly that order.
        candidates = [
            *self._accepts(state, needs, prices),
            *self._advertise(state, surplus, deficit),
            *self._offers(state, needs, prices, surplus, deficit),
            *self._withdrawals(state, prices, surplus, deficit),
        ]
        ranked = sorted(candidates, key=lambda action: -action.score)
        return self._within_limits(state, ranked)

    def _within_limits(self, state, ranked: list) -> list:
        """Obey the run's command limits, best-scoring first.

        Every command we send stores a result, and the run caps how many it
        stores, so capacity is the hard ceiling: once it is gone we send
        nothing, whatever the score.
        """
        budget = min(state.rules.new_commands_per_station_per_tick, store.remaining_result_capacity(state))
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


#: Bundle field order, which is also the order tuples are written in.
_BUNDLE_ORDER = (
    model.Resource.RESOURCE_WATER,
    model.Resource.RESOURCE_FOOD,
    model.Resource.RESOURCE_COMPONENTS,
)
