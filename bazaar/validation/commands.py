"""Builds the six messages a Bazaar client sends.

``ClientMessage`` in bazaar.proto has exactly six arms -- advertise, offer,
accept, withdraw, ready, and sync -- so :class:`CommandBuilder` has exactly six
methods.  Each returns a ``model.ClientMessage`` with the right arm set, ready
to hand to ``encode.py``.

Assembling messages is all this module does.  It validates nothing: checking
request-ID format, expiry bounds, and the server's command limits belongs to
``limits.py``, and reading server messages belongs to ``decode.py``.

``model`` is this module's only import.  It is assumed to mirror bazaar.proto:
same message and enum names, constructed with keyword arguments named exactly
as the proto fields.
"""

from . import model

#: Every message carries this, and the server rejects anything else.
PROTOCOL_VERSION = "2.0"


def _resource_list(resources):
    """Resource lists as ``model.py`` holds them.

    bazaar.proto wraps these in ``ListResource``, which holds the real list
    under ``items``.  Whether ``model.py`` keeps that wrapper or flattens it is
    the one open question in the model contract; this assumes flattened, with
    ``encode.py`` adding the wrapper.  If it keeps the wrapper, this becomes::

        return model.ListResource(items=list(resources))

    This is the only place in the file that depends on the answer.
    """
    return list(resources)


class CommandBuilder:
    """Assembles client messages for one run.

    ``run_id`` is copied from the first ``state`` and repeated in every message,
    so the builder holds it.  ``request_id`` labels a single command, so callers
    pass it per call.
    """

    def __init__(self, run_id, protocol_version=PROTOCOL_VERSION):
        self.run_id = run_id
        self.protocol_version = protocol_version

    def advertise(self, request_id, selling, seeking, expires_tick):
        """Publish what we claim to sell and seek.

        ``selling`` and ``seeking`` are sequences of ``model.Resource``; either
        may be empty, which advertises nothing on that side.
        """
        body = model.AdvertiseBody(
            selling=_resource_list(selling),
            seeking=_resource_list(seeking),
            expires_tick=expires_tick,
        )
        return model.ClientMessage(
            advertise=model.Advertise(
                type=model.AdvertiseType.ADVERTISE_TYPE_ADVERTISE,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
                request_id=request_id,
                body=body,
            )
        )

    def offer(self, request_id, recipient_id, give, receive, expires_tick):
        """Propose exact terms to one planet.

        ``give`` and ``receive`` are ``model.Bundle`` objects, from the
        proposer's point of view: ``give`` is what we pay, ``receive`` is what
        we ask for.  A ``receive`` of all zeros is a gift.
        """
        body = model.OfferBody(
            recipient_id=recipient_id,
            give=give,
            receive=receive,
            expires_tick=expires_tick,
        )
        return model.ClientMessage(
            offer=model.OfferCommand(
                type=model.OfferCommandType.OFFER_COMMAND_TYPE_OFFER,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
                request_id=request_id,
                body=body,
            )
        )

    def accept(self, request_id, offer_id):
        """Accept an offer addressed to us, settling both sides at once."""
        return model.ClientMessage(
            accept=model.Accept(
                type=model.AcceptType.ACCEPT_TYPE_ACCEPT,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
                request_id=request_id,
                body=model.AcceptBody(offer_id=offer_id),
            )
        )

    def withdraw(self, request_id, object_id):
        """Withdraw one of our own advertisements or open offers."""
        return model.ClientMessage(
            withdraw=model.Withdraw(
                type=model.WithdrawType.WITHDRAW_TYPE_WITHDRAW,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
                request_id=request_id,
                body=model.WithdrawBody(object_id=object_id),
            )
        )

    def ready(self, snapshot_sequence, ready=True):
        """Declare that we have read the state and are ready to continue.

        Required on every connection, including reconnects, and it echoes the
        ``snapshot_sequence`` of the state we just read.  Has no ``request_id``
        and no body message.
        """
        return model.ClientMessage(
            ready=model.Ready(
                type=model.ReadyType.READY_TYPE_READY,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
                ready=ready,
                snapshot_sequence=snapshot_sequence,
            )
        )

    def sync(self):
        """Ask for a fresh state.  Has no ``request_id`` and no body."""
        return model.ClientMessage(
            sync=model.Sync(
                type=model.SyncType.SYNC_TYPE_SYNC,
                protocol_version=self.protocol_version,
                run_id=self.run_id,
            )
        )
