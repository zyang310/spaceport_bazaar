"""The game itself: stations, ticks, and the rules every command is judged by.

A plain object with no clock and no I/O.  ``server.py`` decides when a tick
happens and who is connected; the tests drive this directly, tick by tick.

It follows the protocol guide and what the live game was seen to do (see
``runs/``), because a sandbox that is kinder than the real thing would hide
exactly the bugs it exists to find:

* A state shows the offers and transactions **we are party to**, with their
  whole history, and every station's **active** advertisements.
* Every newly processed command advances ``world_version``, rejections
  included; a request-ID conflict and an exact retry do not.
* Proposing an offer reserves nothing.  Both sides must hold their goods at the
  moment of acceptance, or it fails with ``INSUFFICIENT_RESOURCES``.
* Health falls by ``shortage_damage_per_unit`` for each unit of upkeep that
  could not be paid, and a station at zero health has failed for good.
"""

from dataclasses import dataclass, field, replace

from .. import config
from ..validation import model

PROTOCOL_VERSION = "2.0"

R = model.Resource
RESOURCES = (R.RESOURCE_WATER, R.RESOURCE_FOOD, R.RESOURCE_COMPONENTS)
C = model.ResultCode
OPEN, ACCEPTED, WITHDRAWN, OFFER_EXPIRED, OFFER_ENDED = (
    model.OfferStatus.OFFER_STATUS_OPEN,
    model.OfferStatus.OFFER_STATUS_ACCEPTED,
    model.OfferStatus.OFFER_STATUS_WITHDRAWN,
    model.OfferStatus.OFFER_STATUS_EXPIRED,
    model.OfferStatus.OFFER_STATUS_RUN_ENDED,
)
P = model.PublicationStatus


def _zero() -> dict:
    return {r: 0 for r in RESOURCES}


def _bundle(amounts: dict) -> model.Bundle:
    return model.Bundle(*(amounts[r] for r in RESOURCES))


@dataclass
class Station:
    """One station's private books.  Mutable: the world changes it every tick."""

    station_id: str
    specialty: model.Resource
    inventory: dict
    health: int
    failed: bool = False
    first_failure_tick: int | None = None
    last_production: dict = field(default_factory=_zero)
    last_unmet: dict = field(default_factory=_zero)
    fully_supplied_ticks: int = 0
    shortage_ticks: int = 0
    current_shortage_streak: int = 0
    longest_shortage_streak: int = 0
    produced_total: dict = field(default_factory=_zero)
    consumed_total: dict = field(default_factory=_zero)
    unmet_total: dict = field(default_factory=_zero)
    imported_total: dict = field(default_factory=_zero)
    exported_total: dict = field(default_factory=_zero)
    #: request_id -> (the command, its result), in the order they arrived.
    records: dict = field(default_factory=dict)
    commands_this_tick: int = 0
    #: True once a client has connected as this station; bots leave it alone.
    claimed: bool = False

    def holds(self, bundle: model.Bundle) -> bool:
        return all(self.inventory[r] >= bundle.get(r) for r in RESOURCES)


class World:
    """One run of the game, from PHASE_READY to PHASE_FINISHED."""

    def __init__(self, settings: config.SandboxSettings = config.SANDBOX, run_id: str | None = None):
        self.settings = s = settings
        self.run_id = run_id or f"sandbox-{s.seed}"
        self.rules = model.PublicRules(
            rules_version="2.0",
            duration_ticks=s.duration_ticks,
            tick_duration_ms=s.tick_duration_ms,
            resource_order=list(RESOURCES),
            max_health=s.max_health,
            shortage_damage_per_unit=s.shortage_damage_per_unit,
            recovery_per_fully_supplied_tick=s.recovery_per_fully_supplied_tick,
            max_publication_ttl_ticks=s.max_publication_ttl_ticks,
            max_offer_ttl_ticks=s.max_offer_ttl_ticks,
            new_commands_per_station_per_tick=s.new_commands_per_station_per_tick,
            max_request_records_per_station=s.max_request_records_per_station,
            max_open_outgoing_offers=s.max_open_outgoing_offers,
            max_command_bytes=s.max_command_bytes,
        )
        self.upkeep = {r: s.upkeep_per_tick for r in RESOURCES}
        self.tick = 0
        self.version = 1
        self.phase = model.Phase.PHASE_READY
        # Specialties rotate through the resources, so every one has makers.
        self.stations: dict[str, Station] = {}
        for n in range(1, s.stations + 1):
            station_id = f"P{n:02d}"
            self.stations[station_id] = Station(
                station_id=station_id,
                specialty=RESOURCES[(n - 1) % len(RESOURCES)],
                inventory={r: s.start_inventory for r in RESOURCES},
                health=s.max_health,
            )
        self.offers: dict[str, model.Offer] = {}
        self.advertisements: dict[str, model.Advertisement] = {}
        self.transactions: list[model.Transaction] = []

    # --- the clock ------------------------------------------------------------
    def start(self) -> None:
        if self.phase is model.Phase.PHASE_READY:
            self.phase = model.Phase.PHASE_RUNNING
            self.version += 1

    def advance(self) -> None:
        """One tick: production, upkeep, damage, expiry, and the end of the run."""
        if self.phase is not model.Phase.PHASE_RUNNING:
            return
        self.tick += 1
        for station in self.stations.values():
            station.commands_this_tick = 0
            if not station.failed:
                self._produce_and_consume(station)
        for offer in list(self.offers.values()):
            if offer.status is OPEN and self.tick >= offer.expires_tick:
                self.offers[offer.offer_id] = replace(offer, status=OFFER_EXPIRED, closed_tick=self.tick)
        for ad in list(self.advertisements.values()):
            if ad.status is P.PUBLICATION_STATUS_ACTIVE and self.tick >= ad.expires_tick:
                self.advertisements[ad.advertisement_id] = replace(ad, status=P.PUBLICATION_STATUS_EXPIRED)
        self.version += 1
        if self.tick >= self.rules.duration_ticks:
            self._finish()

    def _produce_and_consume(self, station: Station) -> None:
        made = self.settings.production_per_tick
        station.inventory[station.specialty] += made
        station.produced_total[station.specialty] += made
        station.last_production = _zero() | {station.specialty: made}

        unmet = _zero()
        for r in RESOURCES:
            eaten = min(self.upkeep[r], station.inventory[r])
            station.inventory[r] -= eaten
            station.consumed_total[r] += eaten
            unmet[r] = self.upkeep[r] - eaten
            station.unmet_total[r] += unmet[r]
        station.last_unmet = unmet

        short = sum(unmet.values())
        if short:
            station.shortage_ticks += 1
            station.current_shortage_streak += 1
            station.longest_shortage_streak = max(station.longest_shortage_streak, station.current_shortage_streak)
            station.health = max(0, station.health - short * self.rules.shortage_damage_per_unit)
        else:
            station.fully_supplied_ticks += 1
            station.current_shortage_streak = 0
            station.health = min(self.rules.max_health, station.health + self.rules.recovery_per_fully_supplied_tick)
        if station.health == 0:
            station.failed = True
            if station.first_failure_tick is None:
                station.first_failure_tick = self.tick

    def _finish(self) -> None:
        self.phase = model.Phase.PHASE_FINISHED
        for offer in list(self.offers.values()):
            if offer.status is OPEN:
                self.offers[offer.offer_id] = replace(offer, status=OFFER_ENDED, closed_tick=self.tick)
        for ad in list(self.advertisements.values()):
            if ad.status is P.PUBLICATION_STATUS_ACTIVE:
                self.advertisements[ad.advertisement_id] = replace(ad, status=P.PUBLICATION_STATUS_RUN_ENDED)
        self.version += 1

    # --- commands --------------------------------------------------------------
    def command(self, station_id: str, command) -> model.Result | model.ProtocolError:
        """Judge one trading command (the inner ``Advertise``/``OfferCommand``/...).

        Readiness is per connection, so the server checks it before calling
        this; everything that depends only on the world is decided here.
        """
        if command.protocol_version != PROTOCOL_VERSION:
            return self._protocol_error(command, model.ControlCode.CONTROL_CODE_UNSUPPORTED_VERSION, close=True)
        if command.run_id != self.run_id:
            return self._protocol_error(command, model.ControlCode.CONTROL_CODE_RUN_MISMATCH, close=True)
        station = self.stations[station_id]

        stored = station.records.get(command.request_id)
        if stored is not None:
            previous, result = stored
            if previous == command:
                return result  # an exact retry: the stored answer, nothing done twice
            return self._result(command, C.RESULT_CODE_REQUEST_ID_CONFLICT, version=self.version)
        if len(station.records) >= self.rules.max_request_records_per_station:
            return self._protocol_error(command, model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, close=False)

        result = self._process(station, command)
        station.records[command.request_id] = (command, result)
        return result

    def _process(self, station: Station, command) -> model.Result:
        if self.phase is not model.Phase.PHASE_RUNNING:
            return self._reject(command, C.RESULT_CODE_RUN_NOT_RUNNING)
        if station.failed:
            return self._reject(command, C.RESULT_CODE_STATION_FAILED)
        if station.commands_this_tick >= self.rules.new_commands_per_station_per_tick:
            return self._reject(command, C.RESULT_CODE_RATE_LIMITED, retry_after_tick=self.tick + 1)
        station.commands_this_tick += 1
        handler = {
            model.Advertise: self._advertise,
            model.OfferCommand: self._offer,
            model.Accept: self._accept,
            model.Withdraw: self._withdraw,
        }[type(command)]
        return handler(station, command)

    def _expiry_problem(self, expires_tick: int, ttl: int) -> model.ResultCode | None:
        if expires_tick <= self.tick:
            return C.RESULT_CODE_EXPIRED
        if expires_tick > self.tick + ttl:
            return C.RESULT_CODE_INVALID_ARGUMENT
        return None

    def _advertise(self, station: Station, command: model.Advertise) -> model.Result:
        body = command.body
        if len(set(body.selling)) != len(body.selling) or len(set(body.seeking)) != len(body.seeking):
            return self._reject(command, C.RESULT_CODE_INVALID_ARGUMENT)
        problem = self._expiry_problem(body.expires_tick, self.rules.max_publication_ttl_ticks)
        if problem:
            return self._reject(command, problem)
        version = self._bump()
        # Each station has at most one active listing; a new one replaces it.
        for ad in list(self.advertisements.values()):
            if ad.station_id == station.station_id and ad.status is P.PUBLICATION_STATUS_ACTIVE:
                self.advertisements[ad.advertisement_id] = replace(ad, status=P.PUBLICATION_STATUS_REPLACED)
        ad = model.Advertisement(
            advertisement_id=f"advertisement-{version}",
            station_id=station.station_id,
            selling=list(body.selling),
            seeking=list(body.seeking),
            created_tick=self.tick,
            expires_tick=body.expires_tick,
            created_version=version,
            status=P.PUBLICATION_STATUS_ACTIVE,
        )
        self.advertisements[ad.advertisement_id] = ad
        return self._result(command, C.RESULT_CODE_OK, version=version, object_id=ad.advertisement_id)

    def _offer(self, station: Station, command: model.OfferCommand) -> model.Result:
        body = command.body
        if body.recipient_id not in self.stations:
            return self._reject(command, C.RESULT_CODE_NOT_FOUND)
        if body.recipient_id == station.station_id or (body.give.is_zero() and body.receive.is_zero()):
            return self._reject(command, C.RESULT_CODE_INVALID_ARGUMENT)
        problem = self._expiry_problem(body.expires_tick, self.rules.max_offer_ttl_ticks)
        if problem:
            return self._reject(command, problem)
        if len(self.open_offers_from(station.station_id)) >= self.rules.max_open_outgoing_offers:
            return self._reject(command, C.RESULT_CODE_LIMIT_REACHED)
        version = self._bump()
        offer = model.Offer(
            offer_id=f"offer-{version}",
            proposer_id=station.station_id,
            recipient_id=body.recipient_id,
            give=body.give,
            receive=body.receive,
            created_tick=self.tick,
            created_version=version,
            expires_tick=body.expires_tick,
            status=OPEN,
            closed_tick=None,
            transaction_id=None,
        )
        self.offers[offer.offer_id] = offer
        return self._result(command, C.RESULT_CODE_OK, version=version, object_id=offer.offer_id)

    def _accept(self, station: Station, command: model.Accept) -> model.Result:
        offer = self.offers.get(command.body.offer_id)
        if offer is None or offer.recipient_id != station.station_id:
            return self._reject(command, C.RESULT_CODE_NOT_FOUND)
        if offer.status is not OPEN:
            return self._reject(command, C.RESULT_CODE_NOT_OPEN)
        if self.tick >= offer.expires_tick:
            return self._reject(command, C.RESULT_CODE_EXPIRED)
        proposer = self.stations[offer.proposer_id]
        if proposer.failed:
            return self._reject(command, C.RESULT_CODE_STATION_FAILED)
        if not proposer.holds(offer.give) or not station.holds(offer.receive):
            return self._reject(command, C.RESULT_CODE_INSUFFICIENT_RESOURCES)

        version = self._bump()
        for r in RESOURCES:
            paid, asked = offer.give.get(r), offer.receive.get(r)
            proposer.inventory[r] += asked - paid
            station.inventory[r] += paid - asked
            proposer.exported_total[r] += paid
            proposer.imported_total[r] += asked
            station.imported_total[r] += paid
            station.exported_total[r] += asked
        transaction = model.Transaction(
            transaction_id=f"transaction-{version}",
            offer_id=offer.offer_id,
            proposer_id=offer.proposer_id,
            recipient_id=offer.recipient_id,
            give=offer.give,
            receive=offer.receive,
            settled_tick=self.tick,
            settled_version=version,
        )
        self.transactions.append(transaction)
        self.offers[offer.offer_id] = replace(
            offer, status=ACCEPTED, closed_tick=self.tick, transaction_id=transaction.transaction_id
        )
        return self._result(
            command, C.RESULT_CODE_OK, version=version,
            object_id=offer.offer_id, transaction_id=transaction.transaction_id,
        )

    def _withdraw(self, station: Station, command: model.Withdraw) -> model.Result:
        object_id = command.body.object_id
        offer = self.offers.get(object_id)
        ad = self.advertisements.get(object_id)
        if offer is not None and offer.proposer_id == station.station_id:
            if offer.status is not OPEN:
                return self._reject(command, C.RESULT_CODE_NOT_OPEN)
            version = self._bump()
            self.offers[object_id] = replace(offer, status=WITHDRAWN, closed_tick=self.tick)
        elif ad is not None and ad.station_id == station.station_id:
            if ad.status is not P.PUBLICATION_STATUS_ACTIVE:
                return self._reject(command, C.RESULT_CODE_NOT_OPEN)
            version = self._bump()
            self.advertisements[object_id] = replace(ad, status=P.PUBLICATION_STATUS_WITHDRAWN)
        else:
            return self._reject(command, C.RESULT_CODE_NOT_FOUND)
        return self._result(command, C.RESULT_CODE_OK, version=version, object_id=object_id)

    # --- answers -----------------------------------------------------------------
    def _bump(self) -> int:
        self.version += 1
        return self.version

    def _reject(self, command, code: model.ResultCode, retry_after_tick: int | None = None) -> model.Result:
        # A processed rejection is still a revision of the world.
        return self._result(command, code, version=self._bump(), retry_after_tick=retry_after_tick)

    def _result(self, command, code, *, version, object_id=None, transaction_id=None, retry_after_tick=None):
        return model.Result(
            type=model.ResultType.RESULT_TYPE_RESULT,
            protocol_version=PROTOCOL_VERSION,
            run_id=self.run_id,
            request_id=command.request_id,
            ok=code is C.RESULT_CODE_OK,
            code=code,
            processed_tick=self.tick,
            processed_version=version,
            object_id=object_id,
            transaction_id=transaction_id,
            retry_after_tick=retry_after_tick,
        )

    def _protocol_error(self, command, code: model.ControlCode, *, close: bool) -> model.ProtocolError:
        return protocol_error(code, close=close, run_id=command.run_id, request_id=getattr(command, "request_id", None))

    # --- what a station can see ----------------------------------------------------
    def open_offers_from(self, station_id: str) -> list[model.Offer]:
        return [o for o in self.offers.values() if o.proposer_id == station_id and o.status is OPEN]

    def open_offers_to(self, station_id: str) -> list[model.Offer]:
        return [
            o for o in self.offers.values()
            if o.recipient_id == station_id and o.status is OPEN and self.tick < o.expires_tick
        ]

    def active_advertisements(self) -> list[model.Advertisement]:
        return [a for a in self.advertisements.values() if a.status is P.PUBLICATION_STATUS_ACTIVE]

    def state_for(self, station_id: str, snapshot_sequence: int) -> model.State:
        station = self.stations[station_id]
        mine = (station_id,)
        return model.State(
            type=model.StateType.STATE_TYPE_STATE,
            protocol_version=PROTOCOL_VERSION,
            run_id=self.run_id,
            snapshot_sequence=snapshot_sequence,
            world_version=self.version,
            tick=self.tick,
            phase=self.phase,
            self_station_id=station_id,
            rules=self.rules,
            directory=[model.DirectoryEntry(station_id=s, display_name=s) for s in self.stations],
            observation=model.StationObservation(
                station_id=station_id,
                inventory=_bundle(station.inventory),
                health=station.health,
                failed_once=station.first_failure_tick is not None,
                first_failure_tick=station.first_failure_tick,
                last_production=_bundle(station.last_production),
                last_unmet_upkeep=_bundle(station.last_unmet),
                fully_supplied_ticks=station.fully_supplied_ticks,
                shortage_ticks=station.shortage_ticks,
                current_shortage_streak=station.current_shortage_streak,
                longest_shortage_streak=station.longest_shortage_streak,
                produced_total=_bundle(station.produced_total),
                consumed_total=_bundle(station.consumed_total),
                unmet_total=_bundle(station.unmet_total),
                imported_total=_bundle(station.imported_total),
                exported_total=_bundle(station.exported_total),
                upkeep_per_tick=_bundle(self.upkeep),
                specialty=station.specialty,
            ),
            offers=[o for o in self.offers.values() if o.proposer_id in mine or o.recipient_id in mine],
            advertisements=self.active_advertisements(),
            transactions=[t for t in self.transactions if t.proposer_id in mine or t.recipient_id in mine],
            request_results=[result for _, result in station.records.values()],
            outcome=self._outcome(station),
        )

    def _outcome(self, station: Station) -> model.PlayerOutcome | None:
        if self.phase is not model.Phase.PHASE_FINISHED:
            return None
        return model.PlayerOutcome(
            collective_success=not any(s.first_failure_tick is not None for s in self.stations.values()),
            self_failed=station.first_failure_tick is not None,
            aborted=False,
        )


def protocol_error(code: model.ControlCode, *, close: bool, run_id=None, request_id=None) -> model.ProtocolError:
    return model.ProtocolError(
        type=model.ProtocolErrorType.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR,
        protocol_version=PROTOCOL_VERSION,
        run_id=run_id,
        request_id=request_id,
        code=code,
        close_session=close,
    )
