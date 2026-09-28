"""Hivemind: the shared-reserve allocator, played from one station.

Ported from ldmoore/spaceport_hivemind, where one coordinator sees every
station's inventory and issues every station's commands.  Its thesis is
collective survival rather than profit: every station should hold
``reserve_ticks`` of upkeep, and resources move only to restore those reserves.
There are no prices.  Each time it plans, the coordinator:

1. **accepts** every visible offer that is safe to settle;
2. **matches exchanges** -- one station's surplus against another's shortfall,
   both ways in a single offer -- sized to the recipient's delivery window;
3. **gives aid** from whatever genuine surplus is left, at no price.

Every selection updates a running ledger before the next is chosen, so a unit
is never promised twice.  Open offers are deducted before anything counts as
spendable, and a receipt that has not settled never funds another promise.

This policy sees one station, and peer stock is private, so three things change:

* **Advertisements stand in for peer observations.**  A peer that seeks a
  resource is treated as short of it, one that sells it as able to supply it,
  and peer upkeep is assumed to match ours -- the only upkeep we can see.  For
  the same reason this agent always lists its own position.
* **We never dig into our own reserve for a peer.**  The coordinator does, in an
  emergency, because it can see the peer is worse off.  We cannot, so
  everything we pay out comes from stock above our reserve.
* **Paid offers must be complementary.**  The coordinator accepts any safe
  offer, because every offer it sees is one it made.  Here offers come from
  agents with their own agendas, so a paid one is taken only when it brings in
  something we are short of for stock we can spare.

The coordinator allowed one command per station per tick because its demo
server did; this agent spends whatever budget the run's rules give it.
Tunables are in ``bazaar/config.py``.
"""

from dataclasses import dataclass

from ... import actions, config
from ...validation import model
from .. import store

_BUNDLE_ORDER = (
    model.Resource.RESOURCE_WATER,
    model.Resource.RESOURCE_FOOD,
    model.Resource.RESOURCE_COMPONENTS,
)


@dataclass
class _Ledger:
    """One decision's running totals, rebuilt from the state on every call.

    Mutable on purpose: as in the coordinator, each selection is booked before
    the next is chosen.  Nothing outlives the call, so the policy stays a pure
    function of state.
    """

    #: What we may still pay out: stock above our reserve, after promises.
    spare: dict
    #: What we expect to hold once this decision's trades settle.
    stock: dict
    #: Units already on their way to each peer, keyed ``(station, resource)``.
    to_peer: dict


class HivemindPolicy:
    """Keeps every station's reserve topped up, starting with its own."""

    name = "hivemind"

    def __init__(self, weights: config.HivemindWeights = config.DEFAULT_HIVEMIND):
        self.weights = weights

    # --- reserve math ------------------------------------------------------
    def _order(self, state) -> list[model.Resource]:
        return list(state.rules.resource_order) or list(model.Resource)

    def _window(self) -> int:
        """Ticks of upkeep an exchange tops up to; never more than the reserve."""
        return max(0, min(self.weights.survival_ticks, self.weights.reserve_ticks))

    def target(self, state: model.State) -> dict[model.Resource, int]:
        """The reserve each resource should hold: ``reserve_ticks`` of upkeep."""
        upkeep = state.observation.upkeep_per_tick
        return {r: self.weights.reserve_ticks * upkeep.get(r) for r in self._order(state)}

    def committed(self, state: model.State) -> dict[model.Resource, int]:
        """What our open offers promise.  Any of them may settle at any moment."""
        promised = dict.fromkeys(self._order(state), 0)
        for offer in store.my_open_offers(state):
            for resource in promised:
                promised[resource] += offer.give.get(resource)
        return promised

    def pending_receipts(self, state: model.State) -> dict[model.Resource, int]:
        """What our own recent asks should bring in, if the peer answers in time.

        The coordinator counts on a recipient accepting on the very next
        snapshot.  Peers here may be slower or never answer, so an ask counts
        as incoming only for the delivery window; after that we look elsewhere.
        """
        pending = dict.fromkeys(self._order(state), 0)
        for offer in store.my_open_offers(state):
            if state.tick - offer.created_tick < self._window():
                for resource in pending:
                    pending[resource] += offer.receive.get(resource)
        return pending

    def _open_ledger(self, state) -> _Ledger:
        order = self._order(state)
        inventory = state.observation.inventory
        target = self.target(state)
        committed = self.committed(state)
        pending = self.pending_receipts(state)
        to_peer: dict = {}
        for offer in state.offers:
            if offer.recipient_id == state.self_station_id:
                continue
            if not store.is_open(offer) or store.is_expired(offer, state.tick):
                continue
            for resource in order:
                key = (offer.recipient_id, resource)
                to_peer[key] = to_peer.get(key, 0) + offer.give.get(resource)
        return _Ledger(
            spare={r: max(0, inventory.get(r) - committed[r] - target[r]) for r in order},
            stock={r: inventory.get(r) - committed[r] + pending[r] for r in order},
            to_peer=to_peer,
        )

    @staticmethod
    def _gap(have: int, want: int) -> int:
        return max(0, want - have)

    # --- candidates --------------------------------------------------------
    def _accepts(self, state, ledger: _Ledger) -> list:
        """Settle what peers have already offered us, where it is safe to.

        A gift always is: nothing leaves us.  A paid offer must pay only from
        spare stock and bring in something we are short of.
        """
        order = self._order(state)
        target = self.target(state)
        chosen = []
        for offer in sorted(store.incoming_open_offers(state), key=lambda o: o.offer_id):
            received, paid = offer.give, offer.receive
            net_paid = {r: paid.get(r) - received.get(r) for r in order}
            if paid.is_zero():
                reason = f"aid of {received.as_tuple()} from {offer.proposer_id}"
            else:
                if any(net_paid[r] > ledger.spare[r] for r in order):
                    continue  # would pay into our reserve
                if not any(net_paid[r] < 0 and ledger.stock[r] < target[r] for r in order):
                    continue  # brings in nothing we are short of
                reason = f"{received.as_tuple()} for {paid.as_tuple()}, paid from spare stock"
                for resource in order:
                    ledger.spare[resource] -= max(0, net_paid[resource])
            for resource in order:
                ledger.stock[resource] -= net_paid[resource]
            chosen.append(
                actions.Accept(offer_id=offer.offer_id, reason=reason, score=self.weights.accept_score)
            )
        return chosen

    def _withdrawals(self, state) -> list:
        """Pull promises that would now leave us short of one upkeep payment.

        That is the coordinator's hard floor.  Every offer met it when made,
        but upkeep has run since, and a peer can still accept up to the tick it
        expires.  Older promises keep their claim on stock first.
        """
        order = self._order(state)
        upkeep = state.observation.upkeep_per_tick
        left = {r: state.observation.inventory.get(r) for r in order}
        chosen = []
        for offer in sorted(store.my_open_offers(state), key=lambda o: (o.created_tick, o.offer_id)):
            if all(
                offer.give.get(r) == 0
                or left[r] - offer.give.get(r) + offer.receive.get(r) >= upkeep.get(r)
                for r in order
            ):
                for resource in order:
                    left[resource] -= offer.give.get(resource)
                continue
            chosen.append(
                actions.Withdraw(
                    object_id=offer.offer_id,
                    reason=f"paying {offer.give.as_tuple()} would leave less than one tick of upkeep",
                    score=self.weights.withdraw_score,
                )
            )
        return chosen

    def _exchanges(self, state, ledger: _Ledger, peers) -> list:
        """The coordinator's first pass: complementary shortfalls, both ways.

        We ask a peer for what its listing sells and we lack within the
        delivery window, and pay in what it seeks and we hold spare, up to what
        that window would cost the peer from empty.  The largest exchange is
        booked first and the rest are re-matched against what is left.
        """
        order = self._order(state)
        upkeep = state.observation.upkeep_per_tick
        window = self._window()
        chosen = []
        while True:
            best = None
            for advertisement in peers:
                peer = advertisement.station_id
                receive = {
                    r: self._gap(ledger.stock[r], window * upkeep.get(r))
                    if r in advertisement.selling
                    else 0
                    for r in order
                }
                give = {
                    r: min(
                        ledger.spare[r],
                        self._gap(ledger.to_peer.get((peer, r), 0), window * upkeep.get(r)),
                    )
                    if r in advertisement.seeking
                    else 0
                    for r in order
                }
                if not any(receive.values()) or not any(give.values()):
                    continue
                units = sum(receive.values()) + sum(give.values())
                if best is None or units > best[0]:
                    best = (units, peer, give, receive)
            if best is None:
                return chosen
            _, peer, give, receive = best
            self._book(ledger, peer, give, receive)
            chosen.append(
                self._offer(
                    state,
                    peer,
                    give,
                    receive,
                    reason=f"{peer} sells {_names(receive)} and seeks {_names(give)}",
                    score=self.weights.exchange_score,
                )
            )

    def _aid(self, state, ledger: _Ledger, peers) -> list:
        """The coordinator's second pass: spare stock, given away.

        Fills a peer's reserve for what it seeks, less anything already on its
        way to it from anyone.  The largest gift is booked first.
        """
        order = self._order(state)
        upkeep = state.observation.upkeep_per_tick
        reserve = self.weights.reserve_ticks
        chosen = []
        while True:
            best = None
            for advertisement in peers:
                peer = advertisement.station_id
                give = {
                    r: min(
                        ledger.spare[r],
                        self._gap(ledger.to_peer.get((peer, r), 0), reserve * upkeep.get(r)),
                    )
                    if r in advertisement.seeking
                    else 0
                    for r in order
                }
                amount = sum(give.values())
                if amount and (best is None or amount > best[0]):
                    best = (amount, peer, give)
            if best is None:
                return chosen
            _, peer, give = best
            self._book(ledger, peer, give, {})
            chosen.append(
                self._offer(
                    state,
                    peer,
                    give,
                    {},
                    reason=f"{peer} seeks {_names(give)}; we hold it above our reserve",
                    score=self.weights.aid_score,
                )
            )

    def _listing(self, state) -> list:
        """Publish our reserve position; it is the only way peers can see it.

        We sell what is above the reserve and seek what is below it, after our
        open promises.  Republished only when that changes or the listing lapses.
        """
        order = self._order(state)
        inventory = state.observation.inventory
        target = self.target(state)
        committed = self.committed(state)
        level = {r: inventory.get(r) - committed[r] for r in order}
        selling = tuple(r for r in order if level[r] > target[r])
        seeking = tuple(r for r in order if level[r] < target[r])

        current = store.my_active_advertisement(state)
        if current is not None and store.is_expired(current, state.tick):
            current = None
        if not selling and not seeking:
            if current is None:
                return []
            return [
                actions.Withdraw(
                    object_id=current.advertisement_id,
                    reason="every resource sits exactly at its reserve",
                    score=self.weights.advertise_score,
                )
            ]
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
                reason="publish our reserve position"
                if current is None
                else "reserve position changed",
                score=self.weights.advertise_score,
            )
        ]

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _book(ledger: _Ledger, peer: str, give: dict, receive: dict) -> None:
        for resource in ledger.spare:
            paid, got = give.get(resource, 0), receive.get(resource, 0)
            ledger.spare[resource] -= paid
            ledger.stock[resource] += got - paid
            ledger.to_peer[(peer, resource)] = ledger.to_peer.get((peer, resource), 0) + paid

    def _expiry(self, state, ttl_ticks: int | None, ceiling_rule: str) -> int:
        """``None`` means the full lifetime the rules allow."""
        ceiling = getattr(state.rules, ceiling_rule)
        ttl = ceiling if ttl_ticks is None else min(ttl_ticks, ceiling)
        return state.tick + max(1, ttl)

    def _offer(self, state, peer, give: dict, receive: dict, reason: str, score: float):
        return actions.Offer(
            recipient_id=peer,
            give=tuple(give.get(r, 0) for r in _BUNDLE_ORDER),
            receive=tuple(receive.get(r, 0) for r in _BUNDLE_ORDER),
            expires_tick=self._expiry(state, self.weights.offer_ttl_ticks, "max_offer_ttl_ticks"),
            reason=reason,
            score=score,
        )

    # --- the decision --------------------------------------------------------
    def decide(self, state: model.State) -> list:
        """Pure function of the state: same state in, same actions out."""
        if state.phase is not model.Phase.PHASE_RUNNING:
            return []
        observation = state.observation
        if observation.health <= 0 or observation.failed_once:
            return []  # the coordinator drops a station for good once it fails

        ledger = self._open_ledger(state)
        peers = store.peer_active_advertisements(state)
        # Booked in the coordinator's order -- accepts, exchanges, aid -- so
        # each pass only sees what the one before it left.
        candidates = [
            *self._accepts(state, ledger),
            *self._withdrawals(state),
            *self._exchanges(state, ledger, peers),
            *self._aid(state, ledger, peers),
            *self._listing(state),
        ]
        ranked = sorted(candidates, key=lambda action: -action.score)
        return self._within_limits(state, ranked)

    def _within_limits(self, state, ranked: list) -> list:
        """Obey the per-tick budget, stored-result capacity, and offer slots.

        The per-tick budget is shared with every decision already made this
        tick.  Dropping a candidate here never overspends: the ledger booked
        all of them, so what survives is a subset of a plan that fit.
        """
        budget = min(
            state.rules.new_commands_per_station_per_tick - store.commands_used_this_tick(state),
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


def _names(amounts: dict) -> str:
    return "+".join(r.name.removeprefix("RESOURCE_") for r, n in amounts.items() if n) or "nothing"
