"""What a policy decided to do, before it becomes a command.

An action is the brain's output: an intention plus the reason for it.  Turning
one into a ``model.ClientMessage`` needs a ``run_id`` and a ``request_id``,
neither of which the brain owns, so that happens in ``runner.py`` via
``to_message``.

Bundles are ``(water, food, components)`` tuples here, which is how the guide
writes them and how a reason line reads best.
"""

from dataclasses import dataclass, field

from .validation import model


@dataclass(frozen=True)
class Action:
    """Base for the four things a policy can decide to do."""

    #: Why the policy chose this, in a few words, for the decision log.
    reason: str = ""
    #: Ranking score; higher acts first.  Not sent anywhere.
    score: float = 0.0

    def to_message(self, builder, request_id: str) -> model.ClientMessage:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class Advertise(Action):
    """Publish what we claim to sell and seek.  Moves no resources."""

    selling: tuple[model.Resource, ...] = ()
    seeking: tuple[model.Resource, ...] = ()
    expires_tick: int = 0

    def to_message(self, builder, request_id: str) -> model.ClientMessage:
        return builder.advertise(request_id, list(self.selling), list(self.seeking), self.expires_tick)

    def describe(self) -> str:
        sell = "+".join(r.name.removeprefix("RESOURCE_") for r in self.selling) or "nothing"
        seek = "+".join(r.name.removeprefix("RESOURCE_") for r in self.seeking) or "nothing"
        return f"advertise selling {sell}, seeking {seek}, until tick {self.expires_tick}"


@dataclass(frozen=True)
class Offer(Action):
    """Propose exact terms.  ``give`` is what we pay, ``receive`` what we ask."""

    recipient_id: str = ""
    give: tuple[int, int, int] = (0, 0, 0)
    receive: tuple[int, int, int] = (0, 0, 0)
    expires_tick: int = 0

    def to_message(self, builder, request_id: str) -> model.ClientMessage:
        return builder.offer(
            request_id,
            self.recipient_id,
            model.Bundle.of(self.give),
            model.Bundle.of(self.receive),
            self.expires_tick,
        )

    def describe(self) -> str:
        return (
            f"offer {self.give} to {self.recipient_id} for {self.receive}, "
            f"until tick {self.expires_tick}"
        )


@dataclass(frozen=True)
class Accept(Action):
    """Accept an offer addressed to us, settling both sides at once."""

    offer_id: str = ""

    def to_message(self, builder, request_id: str) -> model.ClientMessage:
        return builder.accept(request_id, self.offer_id)

    def describe(self) -> str:
        return f"accept offer {self.offer_id}"


@dataclass(frozen=True)
class Withdraw(Action):
    """Withdraw one of our own advertisements or open offers."""

    object_id: str = ""

    def to_message(self, builder, request_id: str) -> model.ClientMessage:
        return builder.withdraw(request_id, self.object_id)

    def describe(self) -> str:
        return f"withdraw {self.object_id}"
