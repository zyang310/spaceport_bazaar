"""The other stations: simple traders, so the sandbox market is alive.

A bot is not a strategy worth copying.  It is a counterparty with plausible
habits: it keeps a listing up, asks for what it is short of, pays in its
specialty, and answers offers by a rough sense of what each resource is worth to
it right now.  It sometimes hesitates, so offers sit and expire; it sometimes
gives a small gift, and sometimes withdraws an offer nobody took.

Bots send ordinary commands through ``World.command``, so they are held to the
same limits as a client.  All their chance comes from one seeded generator,
which makes a whole sandbox run repeatable.
"""

import random

from ..validation import model
from .world import PROTOCOL_VERSION, RESOURCES, Station, World

#: Most offers a bot keeps open, and most it accepts in one turn: well inside
#: the rules, so a bot never burns its own rate limit.
MAX_OPEN_OFFERS = 6
MAX_ACCEPTS_PER_TURN = 4
#: A bot will not trade a non-specialty resource below this.
KEEP = 5


def bots_act(world: World, rng: random.Random) -> None:
    """Give every station no client has claimed one turn."""
    if world.phase is not model.Phase.PHASE_RUNNING:
        return
    for station in world.stations.values():
        if not station.claimed and not station.failed:
            _Bot(world, station, rng).turn()


class _Bot:
    def __init__(self, world: World, station: Station, rng: random.Random):
        self.world = world
        self.station = station
        self.rng = rng
        self.settings = world.settings

    def turn(self) -> None:
        self.answer_offers()
        self.keep_listed()
        self.ask_for_needs()
        self.maybe_gift()
        self.tidy_up()

    # --- what things are worth to this bot, right now ---------------------------
    def weight(self, resource: model.Resource) -> float:
        if resource is self.station.specialty:
            return 0.4  # it makes more every tick
        have = self.station.inventory[resource]
        short = self.settings.bot_short_below
        return 3.0 if have < short else 1.2 if have < 3 * short else 0.6

    def worth(self, bundle: model.Bundle) -> float:
        return sum(self.weight(r) * bundle.get(r) for r in RESOURCES)

    def needs(self) -> list[model.Resource]:
        """Resources it is short of, most urgent first."""
        short = [
            r for r in RESOURCES
            if r is not self.station.specialty and self.station.inventory[r] < self.settings.bot_short_below
        ]
        return sorted(short, key=lambda r: self.station.inventory[r])

    def leaves_short(self, paying: model.Bundle) -> bool:
        return any(
            r is not self.station.specialty and self.station.inventory[r] - paying.get(r) < KEEP
            for r in RESOURCES
            if paying.get(r)
        )

    # --- the turn ----------------------------------------------------------------
    def answer_offers(self) -> None:
        accepted = 0
        for offer in sorted(self.world.open_offers_to(self.station.station_id), key=lambda o: o.created_version):
            if accepted >= MAX_ACCEPTS_PER_TURN:
                break
            if self.rng.random() > self.settings.bot_accept_chance:
                continue  # not looked at yet; maybe next turn, maybe never
            gets, pays = offer.give, offer.receive
            if not self.station.holds(pays) or self.leaves_short(pays):
                continue
            if self.worth(gets) - self.worth(pays) >= -self.settings.bot_tolerance:
                self.send(model.Accept, model.AcceptBody(offer_id=offer.offer_id))
                accepted += 1

    def keep_listed(self) -> None:
        mine = [a for a in self.world.active_advertisements() if a.station_id == self.station.station_id]
        if mine and mine[0].expires_tick - self.world.tick > 1:
            return
        specialty = self.station.specialty
        selling = [specialty] + [
            r for r in RESOURCES if r is not specialty and self.station.inventory[r] > 3 * self.settings.bot_short_below
        ]
        body = model.AdvertiseBody(
            selling=selling, seeking=self.needs(), expires_tick=self.world.tick + self.rng.randint(3, 6)
        )
        self.send(model.Advertise, body)

    def ask_for_needs(self) -> None:
        needs = self.needs()
        if not needs or self.rng.random() > self.settings.bot_offer_chance:
            return
        if len(self.world.open_offers_from(self.station.station_id)) >= MAX_OPEN_OFFERS:
            return
        want = needs[0]
        sellers = sorted(
            a.station_id for a in self.world.active_advertisements()
            if want in a.selling and a.station_id != self.station.station_id
        )
        # Mostly whoever claims to sell it; sometimes anyone, since a listing is
        # a hint and a station that never advertises should still get offers.
        partner = self.rng.choice(sellers if sellers and self.rng.random() < 0.7 else self.others())
        quantity = self.rng.randint(1, 3)
        price = quantity * self.rng.choice((1, 1, 2, 2, 3))
        if self.station.inventory[self.station.specialty] - price < 10:
            return
        self.offer(partner, pay={self.station.specialty: price}, ask={want: quantity})

    def maybe_gift(self) -> None:
        if self.rng.random() < self.settings.bot_gift_chance:
            amount = self.rng.randint(1, 2)
            if self.station.inventory[self.station.specialty] > 20:
                self.offer(self.rng.choice(self.others()), pay={self.station.specialty: amount}, ask={})

    def tidy_up(self) -> None:
        stale = [
            o for o in self.world.open_offers_from(self.station.station_id)
            if self.world.tick - o.created_tick >= 3
        ]
        if stale and self.rng.random() < 0.3:
            self.send(model.Withdraw, model.WithdrawBody(object_id=stale[0].offer_id))

    # --- sending -------------------------------------------------------------------
    def others(self) -> list[str]:
        return [s for s in self.world.stations if s != self.station.station_id]

    def offer(self, recipient: str, *, pay: dict, ask: dict) -> None:
        body = model.OfferBody(
            recipient_id=recipient,
            give=model.Bundle(*(pay.get(r, 0) for r in RESOURCES)),
            receive=model.Bundle(*(ask.get(r, 0) for r in RESOURCES)),
            expires_tick=self.world.tick + self.rng.randint(2, 5),
        )
        self.send(model.OfferCommand, body)

    def send(self, kind, body) -> None:
        types = {
            model.Advertise: model.AdvertiseType.ADVERTISE_TYPE_ADVERTISE,
            model.OfferCommand: model.OfferCommandType.OFFER_COMMAND_TYPE_OFFER,
            model.Accept: model.AcceptType.ACCEPT_TYPE_ACCEPT,
            model.Withdraw: model.WithdrawType.WITHDRAW_TYPE_WITHDRAW,
        }
        request_id = f"bot-{self.station.station_id}-{len(self.station.records) + 1:05d}"
        command = kind(
            type=types[kind],
            protocol_version=PROTOCOL_VERSION,
            run_id=self.world.run_id,
            request_id=request_id,
            body=body,
        )
        self.world.command(self.station.station_id, command)
