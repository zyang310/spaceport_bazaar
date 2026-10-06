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
STATION_ID = "P08"
RUNS_DIR = ROOT / "runs"

#: Where the token comes from, in preference order.
TOKEN_ENV = "BAZAAR_TOKEN"
ENV_FILE = ROOT / ".env"

#: How long the runner waits for a command's result before retrying it once,
#: and how long it then waits for its own view to catch up once a command
#: settles.  Both are in ticks, not seconds: a fixed number of seconds is
#: generous at the live game's slowest cadence but, at its fastest, can burn
#: several ticks of wall time waiting out one slow reply.  Counting ticks
#: instead keeps the cost the same fraction of the game whatever the speed.
#: ``runner.py`` converts to seconds using the run's own ``tick_duration_ms``.
COMMAND_TIMEOUT_TICKS: float = 2.0
VIEW_CATCHUP_TIMEOUT_TICKS: float = 1.0


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
    decide what a resource is worth once it does.  ``reserve_ticks`` is a hard
    floor under both: no trade may pay a resource out below it.
    """

    #: How many ticks ahead to project upkeep when sizing a need.
    horizon: int = 5
    #: Units to keep on hand beyond projected upkeep.
    buffer: int = 2
    #: Ticks of upkeep we never trade below, however good the deal looks.
    reserve_ticks: int = 3

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
class JesusWeights:
    """How generous the Jesus agent is, and where it draws its lines.

    Every line is in ticks of cover: stock divided by upkeep.
    """

    # --- the lines -----------------------------------------------------------
    #: Nothing is ever paid out below this.
    floor_ticks: float = 3.0
    #: Past this a resource is no longer bought, or taken as a gift.
    enough_ticks: float = 20.0
    #: Above this the rest is surplus, and given away.  The gap from
    #: ``enough_ticks`` stops a resource being bought one tick and given away
    #: the next.  A live run bought 152 water while already holding 90.
    keep_ticks: float = 40.0
    #: Both lines are capped at what is left of the run plus this margin, so
    #: near the end everything the station will not burn goes to someone who
    #: might.
    end_margin_ticks: int = 3

    # --- pricing -------------------------------------------------------------
    #: A short lookahead on purpose: it trades on what is in front of it.  Less
    #: than ``horizon`` ticks of upkeep plus ``buffer`` units on hand is priced
    #: as a deficit, exactly that much as neutral, more as surplus.
    horizon: int = 2
    buffer: int = 1
    #: Deliberately flat, so few trades look unattractive.
    price_deficit: float = 2.0
    price_neutral: float = 1.0
    price_surplus: float = 0.75
    #: The specialty, while any is held.  It refills every tick, so parting
    #: with it costs little.
    price_specialty: float = 0.25
    #: Value it will knowingly give up to keep goods moving: wide enough to
    #: swallow a one-for-two swap of spare goods.
    acceptable_loss: float = 1.0

    # --- buying --------------------------------------------------------------
    #: Paid in the specialty: ``specialty_offer_ratio`` units for each of the
    #: ``specialty_receive_qty`` asked for.  Three for one is roughly what a
    #: live run could afford: about 570 components held or made over 120
    #: ticks, against about 200 water and food to buy.  Lower it if the
    #: specialty runs dry.
    specialty_offer_ratio: int = 3
    specialty_receive_qty: int = 2
    #: Paid in anything else spare: units offered per unit sought, and how
    #: many to ask for.
    offer_ratio: int = 1
    offer_receive_qty: int = 1
    #: Offer the specialty even to a peer whose listing does not seek it.  A
    #: listing is a hint, not a contract.
    unsolicited_specialty: bool = True
    #: Open offers per peer.  Trades count every open offer to that peer;
    #: gifts count only gifts.
    max_offers_per_peer: int = 2

    # --- giving --------------------------------------------------------------
    #: Surplus goes out in lots this size, only to peers whose listing seeks it.
    gift_lot: int = 5

    # --- expiries, in ticks, capped by the run's rules ------------------------
    #: Short-lived, so everything is refreshed often.
    offer_ttl_ticks: int = 3
    gift_ttl_ticks: int = 3
    advertisement_ttl_ticks: int = 3

    # --- ranking -------------------------------------------------------------
    #: Offers for what we need outrank gifts, which outrank listings, which
    #: outrank tidying up.
    offer_bonus: float = 2.0
    gift_score: float = 1.5
    advertise_score: float = 1.0
    withdraw_score: float = 0.25


DEFAULT_JESUS = JesusWeights()


@dataclass(frozen=True)
class DashboardSettings:
    """The live page served beside every run.

    It binds to localhost only: the page shows our inventory and plans, which
    is nobody else's business, and VS Code forwards the port out of the
    devcontainer on its own.
    """

    host: str = "127.0.0.1"
    port: int = 8765

    #: How much of the past each panel keeps.  The page is a glance, not a log;
    #: the full record is in ``runs/``.
    recent_trades: int = 30
    recent_actions: int = 150
    history_ticks: int = 60

    #: Ticks of cover below which a reserve is shown as critical, then low.
    #: Critical matches Jesus's floor; low, Scrooge's danger line.
    critical_cover_ticks: float = 2.0
    low_cover_ticks: float = 5.0
    #: Where each reserve's cover gauge reads full.  Past this, more stock
    #: changes nothing worth looking at.
    cover_gauge_ticks: float = 20.0

    #: The agent panel's two windows, in ticks: the counts, then the strip.
    recent_window_ticks: int = 10
    strip_ticks: int = 30
    #: Highlights the panel lists, and notable changes the hub keeps for it.
    highlights: int = 6
    events_kept: int = 80
    #: A trade settled within this many ticks still shows its cargo lane.
    cargo_ticks: int = 2


DASHBOARD = DashboardSettings()


#: The local sandbox game (``python -m bazaar.sandbox``).  Port 3002, beside the
#: practice server's 3001.
SANDBOX_URL = "ws://127.0.0.1:3002/ws"


@dataclass(frozen=True)
class SandboxSettings:
    """A local game that plays like the live one, for testing without it.

    The rules default to what the live game actually sent (see ``runs/``): nine
    stations starting on (30,30,30), six units of the specialty made per tick,
    one of everything eaten per tick, five health lost per unit short.  Only the
    tick is faster by default, so a whole run fits in two minutes.
    """

    host: str = "127.0.0.1"
    port: int = 3002
    #: Station a client plays unless its token names another (``sandbox-P03``).
    station: str = STATION_ID
    stations: int = 9
    seed: int = 7

    duration_ticks: int = 120
    tick_duration_ms: int = 1000
    #: Seconds in PHASE_READY after the first client declares ready, standing
    #: in for the instructor pressing start.
    start_after_seconds: float = 3.0

    start_inventory: int = 30
    production_per_tick: int = 6
    upkeep_per_tick: int = 1
    max_health: int = 100
    shortage_damage_per_unit: int = 5
    recovery_per_fully_supplied_tick: int = 5
    max_publication_ttl_ticks: int = 12
    max_offer_ttl_ticks: int = 12
    new_commands_per_station_per_tick: int = 10
    max_request_records_per_station: int = 2048
    max_open_outgoing_offers: int = 24
    max_command_bytes: int = 16384

    #: The bots on every station no client has claimed.  A bot is short of a
    #: resource below ``bot_short_below``, and weighs trades accordingly.
    bot_short_below: int = 20
    #: Chance per tick that a bot answers a given offer, or proposes one.  Below
    #: one, so offers sometimes sit, expire, or go to someone else first.
    bot_accept_chance: float = 0.6
    bot_offer_chance: float = 0.7
    #: How much value a bot will give up on a trade it accepts.
    bot_tolerance: float = 0.5
    bot_gift_chance: float = 0.03


SANDBOX = SandboxSettings()
