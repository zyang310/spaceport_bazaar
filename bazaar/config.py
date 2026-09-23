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
