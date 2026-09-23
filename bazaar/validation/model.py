"""Our own vocabulary for every message in bazaar.proto.

This is the domain model the rest of the client speaks.  Nothing here imports
the generated bindings: ``decode.py`` turns wire messages into these types and
``encode.py`` turns these types back into wire messages, and those two files are
the only ones allowed to know Protobuf exists.  See ``docs/architecture.md``.

Two conventions are worth stating because they differ from the schema:

* **Resource lists are plain Python lists.**  The proto wraps them in
  ``ListResource`` because proto2 cannot mark a ``repeated`` field ``required``
  and so cannot tell an empty list from a missing one.  Python already
  distinguishes ``[]`` from ``None``, so the wrapper buys nothing here and
  ``encode.py`` re-adds it on the way out.
* **Nullable wrappers flatten to ``X | None``.**  The proto's two-armed
  ``NullableString`` becomes a plain optional field, and the encode/decode pair
  picks the arm.

``State.self`` in the schema collides with Python's ``self``, so it is named
``observation`` here.
"""

from dataclasses import dataclass, field
from enum import Enum


# --- enums -----------------------------------------------------------------
# Integer values match the proto exactly, so decode.py converts with a plain
# Resource(value) lookup and encode.py writes member.value straight out.

class Resource(Enum):
    RESOURCE_WATER = 1
    RESOURCE_FOOD = 2
    RESOURCE_COMPONENTS = 3


class Phase(Enum):
    PHASE_READY = 1
    PHASE_RUNNING = 2
    PHASE_PAUSED = 3
    PHASE_FINISHED = 4
    PHASE_ABORTED = 5


class OfferStatus(Enum):
    OFFER_STATUS_OPEN = 1
    OFFER_STATUS_ACCEPTED = 2
    OFFER_STATUS_WITHDRAWN = 3
    OFFER_STATUS_EXPIRED = 4
    OFFER_STATUS_RUN_ENDED = 5


class PublicationStatus(Enum):
    PUBLICATION_STATUS_ACTIVE = 1
    PUBLICATION_STATUS_REPLACED = 2
    PUBLICATION_STATUS_WITHDRAWN = 3
    PUBLICATION_STATUS_EXPIRED = 4
    PUBLICATION_STATUS_RUN_ENDED = 5


class ResultCode(Enum):
    RESULT_CODE_OK = 1
    RESULT_CODE_REQUEST_ID_CONFLICT = 2
    RESULT_CODE_RUN_NOT_RUNNING = 3
    RESULT_CODE_RATE_LIMITED = 4
    RESULT_CODE_INVALID_ARGUMENT = 5
    RESULT_CODE_NOT_FOUND = 6
    RESULT_CODE_EXPIRED = 7
    RESULT_CODE_NOT_OPEN = 8
    RESULT_CODE_LIMIT_REACHED = 9
    RESULT_CODE_INSUFFICIENT_RESOURCES = 10
    RESULT_CODE_STATION_FAILED = 11


class ControlCode(Enum):
    CONTROL_CODE_BAD_MESSAGE = 1
    CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED = 2
    CONTROL_CODE_UNSUPPORTED_VERSION = 3
    CONTROL_CODE_RUN_MISMATCH = 4
    CONTROL_CODE_INVALID_AUTHENTICATION = 5
    CONTROL_CODE_SESSION_FENCED = 6


# Single-member enums.  They look silly, but the schema makes each one a
# required field, so every message carries its own.

class AdvertiseType(Enum):
    ADVERTISE_TYPE_ADVERTISE = 1


class OfferCommandType(Enum):
    OFFER_COMMAND_TYPE_OFFER = 1


class AcceptType(Enum):
    ACCEPT_TYPE_ACCEPT = 1


class WithdrawType(Enum):
    WITHDRAW_TYPE_WITHDRAW = 1


class SyncType(Enum):
    SYNC_TYPE_SYNC = 1


class ReadyType(Enum):
    READY_TYPE_READY = 1


class ResultType(Enum):
    RESULT_TYPE_RESULT = 1


class ProtocolErrorType(Enum):
    PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR = 1


class StateType(Enum):
    STATE_TYPE_STATE = 1


class ReadinessType(Enum):
    READINESS_TYPE_READINESS = 1


# --- shared bodies ---------------------------------------------------------

@dataclass(frozen=True)
class Bundle:
    """Named water/food/components quantities, zeros included.

    The guide abbreviates these as ``(water, food, components)``, which is what
    :meth:`as_tuple` and :meth:`of` convert to and from.
    """

    water: int = 0
    food: int = 0
    components: int = 0

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.water, self.food, self.components)

    @classmethod
    def of(cls, values) -> "Bundle":
        water, food, components = values
        return cls(water=water, food=food, components=components)

    def get(self, resource: Resource) -> int:
        """Read one resource by enum, so callers can loop over resources."""
        return getattr(self, _BUNDLE_FIELDS[resource])

    def is_zero(self) -> bool:
        return self.as_tuple() == (0, 0, 0)


#: Maps each resource onto the Bundle attribute that holds it.
_BUNDLE_FIELDS = {
    Resource.RESOURCE_WATER: "water",
    Resource.RESOURCE_FOOD: "food",
    Resource.RESOURCE_COMPONENTS: "components",
}


@dataclass(frozen=True)
class AdvertiseBody:
    selling: list[Resource]
    seeking: list[Resource]
    expires_tick: int


@dataclass(frozen=True)
class OfferBody:
    recipient_id: str
    give: Bundle
    receive: Bundle
    expires_tick: int


@dataclass(frozen=True)
class AcceptBody:
    offer_id: str


@dataclass(frozen=True)
class WithdrawBody:
    object_id: str


# --- commands we send ------------------------------------------------------

@dataclass(frozen=True)
class Advertise:
    type: AdvertiseType
    protocol_version: str
    run_id: str
    request_id: str
    body: AdvertiseBody


@dataclass(frozen=True)
class OfferCommand:
    type: OfferCommandType
    protocol_version: str
    run_id: str
    request_id: str
    body: OfferBody


@dataclass(frozen=True)
class Accept:
    type: AcceptType
    protocol_version: str
    run_id: str
    request_id: str
    body: AcceptBody


@dataclass(frozen=True)
class Withdraw:
    type: WithdrawType
    protocol_version: str
    run_id: str
    request_id: str
    body: WithdrawBody


@dataclass(frozen=True)
class Sync:
    """No ``request_id`` and no body; the schema does not give sync either."""

    type: SyncType
    protocol_version: str
    run_id: str


@dataclass(frozen=True)
class Ready:
    """Echoes the ``snapshot_sequence`` of the state we just read."""

    type: ReadyType
    protocol_version: str
    run_id: str
    ready: bool
    snapshot_sequence: int


#: The arms of ClientMessage, in schema order.
CLIENT_ARMS = ("advertise", "offer", "accept", "withdraw", "sync", "ready")


@dataclass(frozen=True)
class ClientMessage:
    """Exactly one arm is set, mirroring the proto's ``oneof``."""

    advertise: Advertise | None = None
    offer: OfferCommand | None = None
    accept: Accept | None = None
    withdraw: Withdraw | None = None
    sync: Sync | None = None
    ready: Ready | None = None

    def which(self) -> str:
        """Name of the arm that is set.  Raises unless exactly one is."""
        set_arms = [name for name in CLIENT_ARMS if getattr(self, name) is not None]
        if len(set_arms) != 1:
            raise ValueError(f"ClientMessage must set exactly one arm, got {set_arms}")
        return set_arms[0]

    def inner(self):
        """The message held by the arm that is set."""
        return getattr(self, self.which())


# --- messages we receive ---------------------------------------------------

@dataclass(frozen=True)
class Result:
    """The outcome of one command, matched to it by ``request_id``."""

    type: ResultType
    protocol_version: str
    run_id: str
    request_id: str
    ok: bool
    code: ResultCode
    processed_tick: int
    processed_version: int
    object_id: str | None
    transaction_id: str | None
    retry_after_tick: int | None


@dataclass(frozen=True)
class ProtocolError:
    type: ProtocolErrorType
    protocol_version: str
    run_id: str | None
    request_id: str | None
    code: ControlCode
    close_session: bool


@dataclass(frozen=True)
class Offer:
    """Amounts use the proposer's perspective: ``give`` is what it pays."""

    offer_id: str
    proposer_id: str
    recipient_id: str
    give: Bundle
    receive: Bundle
    created_tick: int
    created_version: int
    expires_tick: int
    status: OfferStatus
    closed_tick: int | None
    transaction_id: str | None


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    offer_id: str
    proposer_id: str
    recipient_id: str
    give: Bundle
    receive: Bundle
    settled_tick: int
    settled_version: int


@dataclass(frozen=True)
class Advertisement:
    """A claim about what a station sells or seeks; it moves no resources."""

    advertisement_id: str
    station_id: str
    selling: list[Resource]
    seeking: list[Resource]
    created_tick: int
    expires_tick: int
    created_version: int
    status: PublicationStatus


@dataclass(frozen=True)
class DirectoryEntry:
    station_id: str
    display_name: str


@dataclass(frozen=True)
class StationObservation:
    """Everything the server tells us about our own station.

    Peer stock and production specialties are private, so this only ever
    describes us.
    """

    station_id: str
    inventory: Bundle
    health: int
    failed_once: bool
    first_failure_tick: int | None
    last_production: Bundle
    last_unmet_upkeep: Bundle
    fully_supplied_ticks: int
    shortage_ticks: int
    current_shortage_streak: int
    longest_shortage_streak: int
    produced_total: Bundle
    consumed_total: Bundle
    unmet_total: Bundle
    imported_total: Bundle
    exported_total: Bundle
    upkeep_per_tick: Bundle
    specialty: Resource


@dataclass(frozen=True)
class PublicRules:
    rules_version: str
    duration_ticks: int
    tick_duration_ms: int
    resource_order: list[Resource]
    max_health: int
    shortage_damage_per_unit: int
    recovery_per_fully_supplied_tick: int
    max_publication_ttl_ticks: int
    max_offer_ttl_ticks: int
    new_commands_per_station_per_tick: int
    max_request_records_per_station: int
    max_open_outgoing_offers: int
    max_command_bytes: int


@dataclass(frozen=True)
class PlayerOutcome:
    collective_success: bool | None
    self_failed: bool
    aborted: bool


@dataclass(frozen=True)
class State:
    """A complete picture at one moment; replace the previous view with it.

    The inventory already includes completed trades, so their amounts are never
    added again.  ``self`` in the schema is ``observation`` here, because the
    former is a Python keyword in every method that would touch it.
    """

    type: StateType
    protocol_version: str
    run_id: str
    snapshot_sequence: int
    world_version: int
    tick: int
    phase: Phase
    self_station_id: str
    rules: PublicRules
    directory: list[DirectoryEntry]
    observation: StationObservation
    offers: list[Offer]
    advertisements: list[Advertisement]
    transactions: list[Transaction]
    request_results: list[Result]
    outcome: PlayerOutcome | None


@dataclass(frozen=True)
class Readiness:
    type: ReadinessType
    protocol_version: str
    run_id: str
    ready: bool
    snapshot_sequence: int


#: The arms of ServerMessage, in schema order.
SERVER_ARMS = ("state", "result", "protocol_error", "readiness")


@dataclass(frozen=True)
class ServerMessage:
    """Exactly one arm is set, mirroring the proto's ``oneof``."""

    state: State | None = None
    result: Result | None = None
    protocol_error: ProtocolError | None = None
    readiness: Readiness | None = None

    def which(self) -> str:
        set_arms = [name for name in SERVER_ARMS if getattr(self, name) is not None]
        if len(set_arms) != 1:
            raise ValueError(f"ServerMessage must set exactly one arm, got {set_arms}")
        return set_arms[0]

    def inner(self):
        return getattr(self, self.which())
