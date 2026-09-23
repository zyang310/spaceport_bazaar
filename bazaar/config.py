"""Connection settings and the agent's tunables, all in one place.

A token is a secret, so it is never written here.  It is read from the
``BAZAAR_TOKEN`` environment variable, or from a gitignored ``.env`` beside
this repo, and falls back to a practice-server credentials file only when
neither is set.

The weights below are the agent's whole personality, so they live here rather
than scattered through ``brain/policy/``.  They are deliberately easy to change:
the practice exercise stays at tick 0, so production and upkeep never actually
run and these values barely matter there.  They start mattering in a real game.
"""

import os
from dataclasses import dataclass
from pathlib import Path

#: Repo root, found from this file rather than the working directory.
ROOT = Path(__file__).resolve().parent.parent
STARTER_DIR = ROOT / "artifacts" / "bazaar-protobuf-starter-linux"

#: The live game. Override per run with ``--url``.
DEFAULT_URL = "wss://spaceport.edneo.com/ws"
#: The bundled practice server, which only listens for local connections.
PRACTICE_URL = "ws://127.0.0.1:3001/ws"

CREDENTIALS_PATH = STARTER_DIR / "validation-credentials.json"
REPORT_PATH = STARTER_DIR / "validation-report.json"
STATION_ID = "P01"
RUNS_DIR = ROOT / "runs"

#: Where the token comes from, in preference order.
TOKEN_ENV = "BAZAAR_TOKEN"
ENV_FILE = ROOT / ".env"


def env_token() -> str | None:
    """The token from the environment, or from a gitignored ``.env``.

    The ``.env`` reader is deliberately tiny -- ``KEY=value`` lines, ``#``
    comments -- so that holding a secret outside source control needs no
    extra dependency.  The real environment always wins.
    """
    token = os.environ.get(TOKEN_ENV)
    if token and token.strip():
        return token.strip()
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == TOKEN_ENV:
            return value.strip().strip("'\"") or None
    return None


@dataclass(frozen=True)
class PolicyWeights:
    """What the utility agent cares about, and how much.

    ``horizon`` and ``buffer`` decide what counts as a deficit; the three prices
    decide what a resource is worth once it does.
    """

    #: How many ticks ahead to project upkeep when sizing a need.
    horizon: int = 5
    #: Units to keep on hand beyond projected upkeep.
    buffer: int = 2

    #: Value of a unit we are short of, comfortable with, and holding spare.
    price_deficit: float = 3.0
    price_neutral: float = 1.0
    price_surplus: float = 0.5

    #: Units of surplus offered per unit of need when proposing a trade.
    offer_ratio: int = 2
    #: How much of a needed resource to ask for in one offer.
    offer_receive_qty: int = 1

    #: Ticks ahead to set expiries, capped by the run's rules.
    advertisement_ttl_ticks: int = 6
    offer_ttl_ticks: int = 6

    #: Scores for actions that are not themselves trades, used only for ranking.
    advertise_score: float = 1.0
    withdraw_score: float = 0.75


DEFAULT_WEIGHTS = PolicyWeights()


@dataclass(frozen=True)
class ScroogeWeights:
    """What the Scrooge agent cares about, and how much.

    Scrooge thinks in **ticks of cover** -- how many more ticks its stock would
    survive at the current upkeep -- rather than in abstract need.  That single
    number drives everything: it decides when the agent is willing to trade at
    all, what it will pay, and what it refuses to let go of.

    The agent never gives anything away, so there is no weight here for doing
    so.  Its own reserves come first.
    """

    #: How many ticks ahead to project upkeep.  Longer than the utility
    #: agent's, because reserves are the whole point of this policy.
    horizon: int = 10
    #: Units to keep on hand beyond projected upkeep.
    buffer: int = 2
    #: Never project past the end of the run; hoarding for ticks that will
    #: never happen is waste, not prudence.
    cap_horizon_to_run: bool = True

    #: Below this many ticks of cover a resource is in danger, and only then
    #: is the agent willing to go out and post trades for it.
    danger_ticks: float = 5.0

    #: Value of a unit that is running out, that we are merely short of, that
    #: we are comfortable on, and that we are holding spare.
    price_critical: float = 6.0
    price_deficit: float = 3.0
    price_neutral: float = 1.0
    price_surplus: float = 0.5

    #: The specialty regenerates, so parting with it costs us less than parting
    #: with anything else.  This discount is what makes the agent reach for its
    #: own resource first when it has to pay.  It does not apply when the
    #: specialty is itself in danger.
    specialty_discount: float = 0.5

    #: Before anything is in danger, an incoming trade must return at least
    #: this multiple of what it costs us.  "Clearly advantageous" or nothing.
    advantage_ratio: float = 2.0

    #: Units of payment offered per unit sought when proposing a trade.
    offer_ratio: int = 2
    #: How much of a needed resource to ask for in one offer.
    offer_receive_qty: int = 1

    #: Ticks ahead to set expiries, capped by the run's rules.
    advertisement_ttl_ticks: int = 6
    offer_ttl_ticks: int = 6

    #: Scores for actions that are not themselves trades, used only for ranking.
    advertise_score: float = 1.0
    withdraw_score: float = 0.75


DEFAULT_SCROOGE = ScroogeWeights()


@dataclass(frozen=True)
class HustlerWeights:
    """What the Hustler agent cares about, and how much.

    The Hustler's thesis is that volume beats margin: every settled trade moves
    resources toward whoever values them, and standing still earns nothing.  So
    it keeps a listing up at all times, offers to everyone who will look at it,
    and takes any deal that is not actually bad.

    The one thing it will not do is trade itself to death, which is what
    ``floor_ticks`` is for.
    """

    #: A short lookahead on purpose: the Hustler trades on what is in front of
    #: it, not on what it might need in ten ticks.
    horizon: int = 2
    buffer: int = 1

    #: The hard floor, in ticks of cover. Nothing may take a resource below it.
    #: This is the only line the Hustler will not cross.
    floor_ticks: float = 2.0

    #: Prices are deliberately flat, so few trades look unattractive.
    price_deficit: float = 2.0
    price_neutral: float = 1.0
    price_surplus: float = 0.75

    #: How much value the Hustler will knowingly give up to keep a trade
    #: moving. A small loss is the cost of doing business; this is set wide
    #: enough to swallow a one-for-two swap of spare goods, which is the
    #: bread-and-butter trade this agent exists to make.
    acceptable_loss: float = 1.0

    #: Units offered per unit sought, and how much to ask for at a time.
    offer_ratio: int = 1
    offer_receive_qty: int = 1
    #: Several offers may go to the same peer, unlike the other agents.
    max_offers_per_peer: int = 2

    #: Keep listings short-lived so they are refreshed often.
    advertisement_ttl_ticks: int = 3
    offer_ttl_ticks: int = 3

    #: Offers outrank listings, which outrank tidying up.
    offer_bonus: float = 2.0
    advertise_score: float = 1.0
    withdraw_score: float = 0.25


DEFAULT_HUSTLER = HustlerWeights()
