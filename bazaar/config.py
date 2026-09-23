"""Connection settings and the agent's tunables, all in one place.

The weights below are the agent's whole personality, so they live here rather
than scattered through ``brain/policy/``.  They are deliberately easy to change:
the practice exercise stays at tick 0, so production and upkeep never actually
run and these values barely matter there.  They start mattering in a real game.
"""

from dataclasses import dataclass
from pathlib import Path

#: Repo root, found from this file rather than the working directory.
ROOT = Path(__file__).resolve().parent.parent
STARTER_DIR = ROOT / "artifacts" / "bazaar-protobuf-starter-linux"

#: The practice server only listens for local connections.
DEFAULT_URL = "ws://127.0.0.1:3001/ws"
CREDENTIALS_PATH = STARTER_DIR / "validation-credentials.json"
REPORT_PATH = STARTER_DIR / "validation-report.json"
STATION_ID = "P01"
RUNS_DIR = ROOT / "runs"


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
