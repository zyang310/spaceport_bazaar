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
5. **It pays in what it makes.**  The specialty piles up every tick while
   everything else only drains, so the specialty is the currency: priced low,
   offered first and in bulk, offered even to peers who did not ask for it, and
   never bought.  A live run died holding 283 components after spending its
   water on more of them; this is the fix.
6. **It does not hoard.**  Past ``enough_ticks`` of cover it stops buying a
   resource and refuses gifts of it.  Above ``keep_ticks`` the rest is
   surplus: listed, given to any peer whose listing seeks it, and handed to
   any peer who asks, whatever they pay.  A live run bought 152 water while
   already holding 90, because six components for two water priced as even.

The short horizon is deliberate: the Hustler trades on what is in front of it,
not on what it might want ten ticks from now.  Tunables are in
``bazaar/config.py``.
"""

import math

from ... import actions, config
from ...validation import model
from .. import store
from .base import BUNDLE_ORDER, BasePolicy


class HustlerPolicy(BasePolicy):
    """Always listed, always offering, and hard to say no to."""

    name = "hustler"
    default_weights = config.DEFAULT_HUSTLER

    # --- projection ------------------------------------------------------
    def needs(self, state) -> dict:
        observation = state.observation
        weights = self.weights
        return {
            resource: observation.upkeep_per_tick.get(resource) * weights.horizon
            + weights.buffer
            - observation.inventory.get(resource)
            for resource in self._order(state)
        }

    def prices(self, state, needs) -> dict:
        """What a unit of each resource is worth to us right now.

        The specialty is cheap while we still hold any of it, because it is
        ours to spend.  Once it is gone it is priced like anything else.
        """
        weights = self.weights
        currency = self._currency(state)
        priced = {}
        for resource, need in needs.items():
            if resource == currency:
                priced[resource] = weights.price_specialty
            elif need > 0:
                priced[resource] = weights.price_deficit
            elif need < 0:
                priced[resource] = weights.price_surplus
            else:
                priced[resource] = weights.price_neutral
        return priced

    # --- valuation -------------------------------------------------------
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

    def _spare(self, state, cover) -> tuple:
        """Everything we could pay with without going through the floor."""
        return tuple(
            r
            for r in self._order(state)
            if cover[r] > self.weights.floor_ticks
            and state.observation.inventory.get(r) >= self.weights.offer_ratio
        )

    # --- surplus ---------------------------------------------------------
    def _ticks_left(self, state) -> int:
        """What is left of the run, plus a margin for the last tick or two."""
        return max(0, state.rules.duration_ticks - state.tick) + self.weights.end_margin_ticks

    def _enough_line(self, state) -> float:
        """Ticks of cover past which a resource is no longer worth buying."""
        return min(self.weights.enough_ticks, self._ticks_left(state))

    def _surplus(self, state) -> dict:
        """Units of each resource above the keep line, which we will not miss.

        The line is ``keep_ticks`` of cover, or what the rest of the run will
        burn if that is less, and never below the floor.  A resource with no
        upkeep is never surplus: nothing says what it is for.
        """
        weights = self.weights
        line = max(weights.floor_ticks, min(weights.keep_ticks, self._ticks_left(state)))
        observation = state.observation
        surplus = {}
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            held = observation.inventory.get(resource)
            surplus[resource] = max(0, math.floor(held - line * upkeep)) if upkeep > 0 else 0
        return surplus

    def _unpromised(self, state, surplus) -> dict:
        """Surplus not already promised by one of our open offers.

        Offers reserve nothing and any of them may settle, so whatever they
        give is treated as already gone.  Otherwise the same water could be
        offered to five peers at once.
        """
        left = dict(surplus)
        for offer in store.my_open_offers(state):
            if not store.is_expired(offer, state.tick):
                self._take(left, offer.give)
        return left

    @staticmethod
    def _take(left: dict, bundle: model.Bundle) -> None:
        for resource in left:
            left[resource] = max(0, left[resource] - bundle.get(resource))

    def _currency(self, state) -> model.Resource | None:
        """The specialty, while we still hold any of it.

        Judged purely by what is in the hold, never by how fast it might
        refill.  Production varies from run to run and even tick to tick, so
        it is not something to project -- a balance that looks thin is read
        as spent on purpose, right up until it actually runs out, at which
        point it is just another resource we are short of, bought and
        guarded like one.
        """
        specialty = state.observation.specialty
        return specialty if state.observation.inventory.get(specialty) > 0 else None

    def _wanted(self, state, cover) -> tuple:
        """What we would take: anything we do not yet hold enough of.

        The line is ticks of cover, not the short horizon's need, which would
        ignore food at seven ticks of cover while components pile up; prices
        already rank what is shortest first.  Never the currency itself:
        buying more of it only turns something that drains into something
        that piles up.
        """
        currency = self._currency(state)
        line = self._enough_line(state)
        return tuple(r for r in self._order(state) if r != currency and cover[r] < line)

    def _selling(self, state, cover, surplus) -> tuple:
        """What to list for sale: the currency, and any surplus.

        Surplus is listed so a peer short of it can find us.  That does not
        invite peers to trade our water away for more of what we make, because
        ``_accepts`` only lets a peer pay in the currency when all it takes is
        surplus, which we would give away anyway.  With no currency, anything
        spare is listed.
        """
        currency = self._currency(state)
        if currency is None:
            return self._spare(state, cover)
        return tuple(r for r in self._order(state) if r == currency or surplus[r] > 0)

    # --- candidates ------------------------------------------------------
    def _accepts(self, state, cover, prices, wanted, left) -> list:
        """Take nearly everything: the bar is a small loss, not a profit.

        A peer asking only for our surplus gets it, whatever it pays, since we
        would give it away anyway; that surplus comes out of ``left`` so no
        gift planned later promises it again.  Anything that brings in only
        what we already hold enough of is refused, gifts included: the giver's
        surplus should reach a station that needs it.

        And we never pay for our own specialty.  The acceptable loss is wide
        enough that one water for one component would clear it, and that
        trade, repeated, is how a station starves on a full warehouse.
        """
        weights = self.weights
        currency = self._currency(state)
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            received, paid = offer.give, offer.receive
            worth = self.value(prices, received) - self.value(prices, paid)
            brings_wanted = any(received.get(r) > 0 for r in wanted)
            if paid.is_zero():
                if brings_wanted:
                    chosen.append(
                        actions.Accept(
                            offer_id=offer.offer_id,
                            reason=f"free {received.as_tuple()} from {offer.proposer_id}",
                            score=worth + weights.price_deficit,
                        )
                    )
                continue
            if all(paid.get(r) <= left[r] for r in left):
                self._take(left, paid)
                chosen.append(
                    actions.Accept(
                        offer_id=offer.offer_id,
                        reason=f"{paid.as_tuple()} of surplus to {offer.proposer_id}",
                        score=weights.gift_score,
                    )
                )
                continue
            if not brings_wanted:
                continue
            if currency is not None and received.get(currency) > 0:
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

    def _terms(self, state, cover, advertisement, want) -> list:
        """Ways to pay a peer for ``want``, best first, as ``(pay, receive, give)``.

        The currency leads, in its own lot size, and goes whether or not the
        peer's listing seeks it.  Anything else must be spare and sought.
        """
        weights = self.weights
        currency = self._currency(state)
        spare = self._spare(state, cover)
        terms = []
        if currency is not None and (
            weights.unsolicited_specialty or currency in advertisement.seeking
        ):
            terms.append(
                (
                    currency,
                    weights.specialty_receive_qty,
                    weights.specialty_offer_ratio * weights.specialty_receive_qty,
                )
            )
        for pay in self._order(state):
            if pay in (want, currency) or pay not in advertisement.seeking or pay not in spare:
                continue
            terms.append((pay, weights.offer_receive_qty, weights.offer_ratio))
        return terms

    def _proposal(self, state, cover, prices, advertisement, want):
        """The best offer we can make a peer for ``want``, or ``None``.

        Only one per resource sought: once the currency can pay, settling for
        water as well just swaps one drain for another.
        """
        weights = self.weights
        for pay, receive_qty, give_qty in self._terms(state, cover, advertisement, want):
            receive = model.Bundle.of(
                tuple(receive_qty if r == want else 0 for r in BUNDLE_ORDER)
            )
            give = model.Bundle.of(tuple(give_qty if r == pay else 0 for r in BUNDLE_ORDER))
            if give.is_zero() or not self._affordable(state, give):
                continue
            if self._breaches_floor(state, cover, receive, give):
                continue
            worth = self.value(prices, receive) - self.value(prices, give)
            if worth < -weights.acceptable_loss:
                continue
            unasked = "" if pay in advertisement.seeking else ", unasked"
            return actions.Offer(
                recipient_id=advertisement.station_id,
                give=give.as_tuple(),
                receive=receive.as_tuple(),
                expires_tick=self._expiry(state, weights.offer_ttl_ticks, "max_offer_ttl_ticks"),
                reason=(
                    f"{advertisement.station_id} sells "
                    f"{want.name.removeprefix('RESOURCE_')} for "
                    f"{pay.name.removeprefix('RESOURCE_')}{unasked}"
                ),
                score=worth + weights.offer_bonus,
            )
        return None

    def _offers(self, state, cover, prices, wanted) -> list:
        """Offer to everyone who sells something we want.

        Several offers may go to the same peer, one per resource they sell: the
        point is to have as many live proposals out as the rules allow.
        """
        weights = self.weights
        open_per_peer = {}
        for mine in store.my_open_offers(state):
            open_per_peer[mine.recipient_id] = open_per_peer.get(mine.recipient_id, 0) + 1

        chosen = []
        for advertisement in store.peer_active_advertisements(state):
            room = weights.max_offers_per_peer - open_per_peer.get(advertisement.station_id, 0)
            made = 0
            for want in self._order(state):
                if made >= room:
                    break
                if want not in advertisement.selling or want not in wanted:
                    continue
                proposal = self._proposal(state, cover, prices, advertisement, want)
                if proposal is not None:
                    made += 1
                    chosen.append(proposal)
        return chosen

    def _gifts(self, state, left) -> list:
        """Give surplus to the peers asking for it.

        Only to a peer whose listing seeks the resource; one that has not
        asked is as likely to leave it sitting as to need it.  One new lot per
        peer per resource each tick, so several askers share the surplus, and
        each lot comes out of ``left`` as it is planned.
        """
        weights = self.weights
        open_gifts = {}
        for mine in store.my_open_offers(state):
            if mine.receive.is_zero():
                open_gifts[mine.recipient_id] = open_gifts.get(mine.recipient_id, 0) + 1

        chosen = []
        for resource in self._order(state):
            for advertisement in store.peer_active_advertisements(state):
                station = advertisement.station_id
                if resource not in advertisement.seeking:
                    continue
                if open_gifts.get(station, 0) >= weights.max_offers_per_peer:
                    continue
                lot = min(weights.gift_lot, left[resource])
                if lot <= 0:
                    break
                left[resource] -= lot
                open_gifts[station] = open_gifts.get(station, 0) + 1
                chosen.append(
                    actions.Offer(
                        recipient_id=station,
                        give=tuple(lot if r == resource else 0 for r in BUNDLE_ORDER),
                        receive=(0, 0, 0),
                        expires_tick=self._expiry(
                            state, weights.gift_ttl_ticks, "max_offer_ttl_ticks"
                        ),
                        reason=(
                            f"gift to {station}, which seeks "
                            f"{resource.name.removeprefix('RESOURCE_')}"
                        ),
                        score=weights.gift_score,
                    )
                )
        return chosen

    def _advertise(self, state, cover, surplus, wanted) -> list:
        """Keep a listing up at all times; a peer who cannot find us cannot trade."""
        selling = self._selling(state, cover, surplus)
        seeking = wanted
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

    def _withdrawals(self, state, prices, surplus) -> list:
        """Only pull an offer that has become genuinely bad, and reluctantly.

        A gift is meant to lose value, so it is judged differently: it stays
        while the surplus still covers it, and is pulled once it does not.
        """
        room = dict(surplus)
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: o.offer_id):
            if offer.receive.is_zero():
                if all(offer.give.get(r) <= room[r] for r in room):
                    self._take(room, offer.give)
                    continue
                reason = "we can no longer spare this gift"
            else:
                worth = self.value(prices, offer.receive) - self.value(prices, offer.give)
                if worth >= -self.weights.acceptable_loss:
                    continue
                reason = f"our offer is now worth {worth:+.1f}"
            chosen.append(
                actions.Withdraw(
                    object_id=offer.offer_id,
                    reason=reason,
                    score=self.weights.withdraw_score,
                )
            )
        return chosen

    # --- the decision ----------------------------------------------------
    def candidates(self, state) -> list:
        cover = self.cover(state)
        needs = self.needs(state)
        prices = self.prices(state, needs)
        wanted = self._wanted(state, cover)
        surplus = self._surplus(state)
        # What gifts may still promise: surplus less our open offers, less
        # whatever this tick's accepts and offers would pay out.
        left = self._unpromised(state, surplus)
        accepts = self._accepts(state, cover, prices, wanted, left)
        offers = self._offers(state, cover, prices, wanted)
        for offer in offers:
            self._take(left, model.Bundle.of(offer.give))
        return [
            *accepts,
            *offers,
            *self._gifts(state, left),
            *self._advertise(state, cover, surplus, wanted),
            *self._withdrawals(state, prices, surplus),
        ]
