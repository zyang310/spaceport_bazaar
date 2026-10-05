"""Jesus: trade freely, keep enough, give the rest away.

Every settled trade moves goods toward whoever needs them more, so this agent
is always listed, offers to every peer selling something it lacks, and takes
any such trade that is not actually bad.  It pays in its specialty, which
refills every tick, and never buys that back while any remains.

Three lines per resource, in ticks of cover, keep the generosity from turning
into either starvation or hoarding:

- **floor** (2): nothing is ever paid out below it.
- **enough** (20): past it a resource is no longer bought, nor taken as a gift.
- **keep** (40): above it the rest is surplus -- listed, given in lots to peers
  whose listing seeks it, and handed to any peer who asks, whatever they pay.

Near the end of the run the upper two shrink to what is left of it, so
everything the station will not burn goes to someone who might.  Tunables are
in ``bazaar/config.py``.
"""

import math
from collections import Counter

from ... import actions, config
from ...validation import model
from .. import store
from .base import BasePolicy, label, one


class JesusPolicy(BasePolicy):
    """Always listed, always offering, and gives away what it will not burn."""

    name = "jesus"
    default_weights = config.DEFAULT_JESUS

    # --- the lines -----------------------------------------------------------
    def _ticks_left(self, state) -> int:
        """What is left of the run, plus a margin for the last tick or two."""
        return max(0, state.rules.duration_ticks - state.tick) + self.weights.end_margin_ticks

    def _enough_line(self, state) -> float:
        return min(self.weights.enough_ticks, self._ticks_left(state))

    def _keep_line(self, state) -> float:
        weights = self.weights
        return max(weights.floor_ticks, min(weights.keep_ticks, self._ticks_left(state)))

    def _breaches_floor(self, state, cover, received, paid) -> bool:
        """Whether a trade leaves anything below the floor and worse off than now.

        A resource already below it is no reason to refuse help with it.
        """
        inventory = state.observation.inventory
        for r in self._order(state):
            after = self._ticks_of(state, r, inventory.get(r) - paid.get(r) + received.get(r))
            if after < self.weights.floor_ticks and after < cover[r]:
                return True
        return False

    # --- what we hold --------------------------------------------------------
    def _currency(self, state) -> model.Resource | None:
        """The specialty, while any is held: what we pay with.

        Judged by the hold, never by expected production, which varies from
        run to run and tick to tick.  Once it runs out it is just another
        resource we lack, bought and guarded like one.
        """
        specialty = state.observation.specialty
        return specialty if state.observation.inventory.get(specialty) > 0 else None

    def prices(self, state) -> dict:
        """What a unit of each resource is worth to us right now.

        The specialty is cheap while any is held.  Anything else is priced by
        whether a short horizon leaves it short.
        """
        weights = self.weights
        observation = state.observation
        currency = self._currency(state)
        priced = {}
        for r in self._order(state):
            need = (
                observation.upkeep_per_tick.get(r) * weights.horizon
                + weights.buffer
                - observation.inventory.get(r)
            )
            if r == currency:
                priced[r] = weights.price_specialty
            elif need > 0:
                priced[r] = weights.price_deficit
            elif need < 0:
                priced[r] = weights.price_surplus
            else:
                priced[r] = weights.price_neutral
        return priced

    def _wanted(self, state, cover) -> tuple:
        """Everything below the enough line, except the specialty.

        Judged in ticks of cover rather than by the short horizon, which would
        ignore food at seven ticks while components pile up.  Buying more of
        the specialty only turns something that drains into something that
        piles up.
        """
        currency = self._currency(state)
        line = self._enough_line(state)
        return tuple(r for r in self._order(state) if r != currency and cover[r] < line)

    def _spare(self, state, cover) -> tuple:
        """Everything we could pay with without going through the floor."""
        return tuple(
            r
            for r in self._order(state)
            if cover[r] > self.weights.floor_ticks
            and state.observation.inventory.get(r) >= self.weights.offer_ratio
        )

    def _surplus(self, state) -> dict:
        """Units of each resource above the keep line.

        A resource with no upkeep is never surplus: nothing says what it is for.
        """
        line = self._keep_line(state)
        observation = state.observation
        surplus = {}
        for r in self._order(state):
            upkeep = observation.upkeep_per_tick.get(r)
            held = observation.inventory.get(r)
            surplus[r] = max(0, math.floor(held - line * upkeep)) if upkeep > 0 else 0
        return surplus

    def _unpromised(self, state, surplus) -> dict:
        """Surplus not already given by one of our open offers.

        Offers reserve nothing and any may settle, so what they give counts as
        gone.  Otherwise the same water could be offered to five peers at once.
        """
        left = dict(surplus)
        for offer in store.my_open_offers(state):
            if not store.is_expired(offer, state.tick):
                _take(left, offer.give)
        return left

    # --- accepting -----------------------------------------------------------
    def _accepts(self, state, cover, prices, wanted, left) -> list:
        """Take nearly everything: the bar is a small loss, not a profit."""
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            verdict = self._verdict(state, cover, prices, wanted, left, offer)
            if verdict is not None:
                reason, score = verdict
                chosen.append(actions.Accept(offer_id=offer.offer_id, reason=reason, score=score))
        return chosen

    def _verdict(self, state, cover, prices, wanted, left, offer) -> tuple | None:
        """Why to accept ``offer`` and how keenly, or ``None`` to let it lie.

        A gift is taken only if it brings something we lack: the giver's
        surplus should reach a station that needs it.  A peer asking only for
        our surplus gets it whatever it pays.  Anything else must bring
        something we lack, must not pay us in our own specialty -- one water
        for one component, repeated, is how a station starves on a full
        warehouse -- and must clear the floor and the acceptable loss.
        """
        weights = self.weights
        received, paid = offer.give, offer.receive
        worth = self.value(prices, received) - self.value(prices, paid)
        brings_wanted = any(received.get(r) > 0 for r in wanted)
        if paid.is_zero():
            if not brings_wanted:
                return None
            return f"free {received.as_tuple()} from {offer.proposer_id}", worth + weights.price_deficit
        if _fits(paid, left):
            _take(left, paid)  # so no gift planned later promises it again
            return f"{paid.as_tuple()} of surplus to {offer.proposer_id}", weights.gift_score
        currency = self._currency(state)
        if (
            not brings_wanted
            or (currency is not None and received.get(currency) > 0)
            or not self._affordable(state, paid)
            or self._breaches_floor(state, cover, received, paid)
            or worth < -weights.acceptable_loss
        ):
            return None
        return f"{received.as_tuple()} for {paid.as_tuple()}, worth {worth:+.1f}", worth

    # --- offering ------------------------------------------------------------
    def _offer(self, state, recipient, give, receive, ttl, reason, score) -> actions.Offer:
        return actions.Offer(
            recipient_id=recipient,
            give=give,
            receive=receive,
            expires_tick=self._expiry(state, ttl, "max_offer_ttl_ticks"),
            reason=reason,
            score=score,
        )

    def _terms(self, state, cover, advertisement, want):
        """Ways to pay a peer for ``want``, best first, as ``(pay, give, receive)``.

        The specialty leads, in its own lot size, whether or not the peer
        seeks it.  Anything else must be spare and sought.
        """
        weights = self.weights
        currency = self._currency(state)
        if currency is not None and (
            weights.unsolicited_specialty or currency in advertisement.seeking
        ):
            qty = weights.specialty_receive_qty
            yield currency, _lot(currency, weights.specialty_offer_ratio * qty), _lot(want, qty)
        spare = self._spare(state, cover)
        for pay in self._order(state):
            if pay not in (want, currency) and pay in advertisement.seeking and pay in spare:
                yield pay, _lot(pay, weights.offer_ratio), _lot(want, weights.offer_receive_qty)

    def _proposal(self, state, cover, prices, advertisement, want) -> actions.Offer | None:
        """The best offer we can make a peer for ``want``, or ``None``.

        Only one per resource sought: once the specialty can pay, settling for
        water as well just swaps one drain for another.
        """
        weights = self.weights
        for pay, give, receive in self._terms(state, cover, advertisement, want):
            if (
                give.is_zero()
                or not self._affordable(state, give)
                or self._breaches_floor(state, cover, receive, give)
            ):
                continue
            worth = self.value(prices, receive) - self.value(prices, give)
            if worth < -weights.acceptable_loss:
                continue
            unasked = "" if pay in advertisement.seeking else ", unasked"
            return self._offer(
                state,
                advertisement.station_id,
                give.as_tuple(),
                receive.as_tuple(),
                weights.offer_ttl_ticks,
                reason=f"{advertisement.station_id} sells {label(want)} for {label(pay)}{unasked}",
                score=worth + weights.offer_bonus,
            )
        return None

    def _offers(self, state, cover, prices, wanted) -> list:
        """Offer to every peer selling something we want, several per peer."""
        open_per_peer = Counter(o.recipient_id for o in store.my_open_offers(state))
        chosen = []
        for advertisement in store.peer_active_advertisements(state):
            room = self.weights.max_offers_per_peer - open_per_peer[advertisement.station_id]
            for want in self._order(state):
                if room <= 0:
                    break
                if want not in advertisement.selling or want not in wanted:
                    continue
                proposal = self._proposal(state, cover, prices, advertisement, want)
                if proposal is not None:
                    chosen.append(proposal)
                    room -= 1
        return chosen

    def _gifts(self, state, left) -> list:
        """Give surplus to the peers whose listing seeks it.

        One new lot per peer per resource each tick, so several askers share
        the surplus; each lot comes out of ``left`` as it is planned.
        """
        weights = self.weights
        open_gifts = Counter(
            o.recipient_id for o in store.my_open_offers(state) if o.receive.is_zero()
        )
        chosen = []
        for resource in self._order(state):
            for advertisement in store.peer_active_advertisements(state):
                station = advertisement.station_id
                if resource not in advertisement.seeking:
                    continue
                if open_gifts[station] >= weights.max_offers_per_peer:
                    continue
                lot = min(weights.gift_lot, left[resource])
                if lot <= 0:
                    break
                left[resource] -= lot
                open_gifts[station] += 1
                chosen.append(
                    self._offer(
                        state,
                        station,
                        one(resource, lot),
                        (0, 0, 0),
                        weights.gift_ttl_ticks,
                        reason=f"gift to {station}, which seeks {label(resource)}",
                        score=weights.gift_score,
                    )
                )
        return chosen

    # --- listing and tidying -------------------------------------------------
    def _selling(self, state, cover, surplus) -> tuple:
        """The specialty and any surplus, so a peer short of either finds us.

        Listing surplus does not let peers trade it away for more of what we
        make: they may pay in our specialty only when all they take is surplus.
        With no specialty left, anything spare is listed.
        """
        currency = self._currency(state)
        if currency is None:
            return self._spare(state, cover)
        return tuple(r for r in self._order(state) if r == currency or surplus[r] > 0)

    def _advertise(self, state, cover, surplus, wanted) -> list:
        """Keep a listing up at all times; a peer who cannot find us cannot trade."""
        selling = self._selling(state, cover, surplus)
        if not selling and not wanted:
            return []
        current = store.my_active_advertisement(state)
        if (
            current is not None
            and tuple(current.selling) == selling
            and tuple(current.seeking) == wanted
        ):
            return []
        return [
            actions.Advertise(
                selling=selling,
                seeking=wanted,
                expires_tick=self._expiry(
                    state, self.weights.advertisement_ttl_ticks, "max_publication_ttl_ticks"
                ),
                reason="stay visible" if current is None else "refresh the listing",
                score=self.weights.advertise_score,
            )
        ]

    def _withdrawals(self, state, prices, surplus) -> list:
        """Pull an offer only once it has gone bad.

        A trade has gone bad when it is worth less than ``-acceptable_loss``.
        A gift is meant to lose value, so it goes bad only once the surplus no
        longer covers it.
        """
        room = dict(surplus)
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: o.offer_id):
            if offer.receive.is_zero():
                if _fits(offer.give, room):
                    _take(room, offer.give)
                    continue
                reason = "we can no longer spare this gift"
            else:
                worth = self.value(prices, offer.receive) - self.value(prices, offer.give)
                if worth >= -self.weights.acceptable_loss:
                    continue
                reason = f"our offer is now worth {worth:+.1f}"
            chosen.append(
                actions.Withdraw(
                    object_id=offer.offer_id, reason=reason, score=self.weights.withdraw_score
                )
            )
        return chosen

    # --- the decision --------------------------------------------------------
    def candidates(self, state) -> list:
        cover = self.cover(state)
        prices = self.prices(state)
        wanted = self._wanted(state, cover)
        surplus = self._surplus(state)
        # What gifts may still promise: surplus less our open offers, less
        # whatever this tick's accepts and offers would pay out.
        left = self._unpromised(state, surplus)
        accepts = self._accepts(state, cover, prices, wanted, left)
        offers = self._offers(state, cover, prices, wanted)
        for offer in offers:
            _take(left, model.Bundle.of(offer.give))
        return [
            *accepts,
            *offers,
            *self._gifts(state, left),
            *self._advertise(state, cover, surplus, wanted),
            *self._withdrawals(state, prices, surplus),
        ]


def _lot(resource: model.Resource, qty: int) -> model.Bundle:
    return model.Bundle.of(one(resource, qty))


def _fits(bundle: model.Bundle, left: dict) -> bool:
    return all(bundle.get(r) <= left[r] for r in left)


def _take(left: dict, bundle: model.Bundle) -> None:
    """Subtract ``bundle`` from ``left`` in place, never below zero."""
    for r in left:
        left[r] = max(0, left[r] - bundle.get(r))
