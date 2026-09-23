"""The authoritative view of the world, replaced wholesale per snapshot.

A state is a complete picture at one moment, so :class:`Store` never merges or
patches: it drops the previous view and keeps the new one.  Inventory already
includes completed trades, so nothing here adds their amounts a second time.

The query functions are plain functions of a state rather than methods, because
the policy must stay a pure function of the state it is handed -- including
states loaded from a fixture file, which never went through a Store.
"""

from ..validation import model


class Store:
    """Holds the latest snapshot and how many we have seen."""

    def __init__(self):
        self.state: model.State | None = None
        self.snapshots = 0

    def replace(self, state: model.State) -> None:
        self.state = state
        self.snapshots += 1

    @property
    def world_version(self) -> int:
        return 0 if self.state is None else self.state.world_version


# --- queries ---------------------------------------------------------------

def is_open(offer: model.Offer) -> bool:
    return offer.status is model.OfferStatus.OFFER_STATUS_OPEN


def is_expired(obj, tick: int) -> bool:
    """``expires_tick`` is the tick at which a thing stops being usable."""
    return tick >= obj.expires_tick


def my_open_offers(state: model.State) -> list[model.Offer]:
    """Our own outgoing offers that are still open."""
    return [
        offer
        for offer in state.offers
        if offer.proposer_id == state.self_station_id and is_open(offer)
    ]


def incoming_open_offers(state: model.State) -> list[model.Offer]:
    """Offers addressed to us and still open, which we could accept."""
    return [
        offer
        for offer in state.offers
        if offer.recipient_id == state.self_station_id
        and offer.proposer_id != state.self_station_id
        and is_open(offer)
        and not is_expired(offer, state.tick)
    ]


def my_active_advertisement(state: model.State) -> model.Advertisement | None:
    """Our current listing, if any.  Each station has at most one active."""
    for advertisement in state.advertisements:
        if (
            advertisement.station_id == state.self_station_id
            and advertisement.status is model.PublicationStatus.PUBLICATION_STATUS_ACTIVE
        ):
            return advertisement
    return None


def peer_active_advertisements(state: model.State) -> list[model.Advertisement]:
    """Everyone else's current listings, sorted by station ID for determinism."""
    peers = [
        advertisement
        for advertisement in state.advertisements
        if advertisement.station_id != state.self_station_id
        and advertisement.status is model.PublicationStatus.PUBLICATION_STATUS_ACTIVE
        and not is_expired(advertisement, state.tick)
    ]
    return sorted(peers, key=lambda a: a.station_id)


def remaining_result_capacity(state: model.State) -> int:
    """Stored-result slots left before the server refuses new commands.

    The server keeps each command's result so retries can recover it, and the
    run caps how many it stores.  Exceeding the cap is a protocol error, not a
    result, so we stop before reaching it.
    """
    used = len(state.request_results)
    return max(0, state.rules.max_request_records_per_station - used)


def remaining_offer_slots(state: model.State) -> int:
    return max(0, state.rules.max_open_outgoing_offers - len(my_open_offers(state)))
