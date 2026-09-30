"""What a policy is, and what every agent shares.

Two shapes live behind this module, because the two kinds of policy answer
different questions.

:class:`Policy` is the real one: a pure function from a state to a list of
actions.  Purity is what makes it testable, since a state captured from a live
run can be replayed offline and must produce the same decision.

:class:`ScriptedDriver` is the exception.  The guide's exercise is a fixed
sequence where each step waits for a specific set of responses -- including two
steps that send nothing at all -- so it has to see results and protocol errors,
not just states.  It drives the connection directly instead of returning
actions.

:class:`BasePolicy` is how an agent implements :class:`Policy`.  The rules every
agent must obey -- the gate, the ranking, the run's command limits -- live here
once, so a new agent gets them by inheriting rather than by copying them and
hoping the copies never drift.  A subclass writes :meth:`BasePolicy.candidates`
and nothing else is required of it.
"""

import abc
import math
from typing import ClassVar, Protocol, final, runtime_checkable

from ... import actions
from ...validation import model
from .. import store

#: Bundle field order, which is also the order tuples are written in.
BUNDLE_ORDER = (
    model.Resource.RESOURCE_WATER,
    model.Resource.RESOURCE_FOOD,
    model.Resource.RESOURCE_COMPONENTS,
)


@runtime_checkable
class Policy(Protocol):
    """Decides what to do, given only what the server reported."""

    name: str

    def decide(self, state: model.State) -> list:
        """Return the actions to take for this state.

        Must be a pure function of ``state`` plus the policy's own config, so
        the same state always produces the same actions.
        """
        ...


class ScriptedDriver(Protocol):
    """Runs a fixed sequence against a live connection."""

    name: str

    async def run(self, client) -> None:
        ...


class BasePolicy(abc.ABC):
    """The shared skeleton of every deciding agent.

    ``decide`` is fixed: gate, generate, rank, limit.  A subclass supplies the
    candidates, and may override ``_within_limits`` when it has a rule of its
    own that must see the chosen actions together, as the utility agent's
    reserve does.  The tests in ``test_policy_contract.py`` hold every
    registered agent to this.
    """

    #: The CLI flag value, and the key in ``AGENTS``.
    name: ClassVar[str]
    #: The weights used when none are passed in, from ``bazaar/config.py``.
    default_weights: ClassVar[object]

    def __init__(self, weights=None):
        self.weights = self.default_weights if weights is None else weights

    # --- the decision ----------------------------------------------------
    @final
    def decide(self, state: model.State) -> list:
        """Pure function of the state: same state in, same actions out."""
        if state.phase is not model.Phase.PHASE_RUNNING:
            return []
        if state.observation.health <= 0:
            return []  # a failed station is permanent; do not trade from it

        # Candidates are generated in resource order, then station order, so
        # the stable sort leaves equal scores in exactly that order.
        ranked = sorted(self.candidates(state), key=lambda action: -action.score)
        return self._within_limits(state, ranked)

    @abc.abstractmethod
    def candidates(self, state: model.State) -> list:
        """Everything this agent would like to do, unranked and unlimited."""

    def _within_limits(self, state, ranked: list) -> list:
        """Obey the run's command limits, best-scoring first.

        Every command stores a result and the run caps how many it keeps, so
        capacity is the hard ceiling: once it is gone nothing is sent.
        """
        budget = min(
            state.rules.new_commands_per_station_per_tick,
            store.remaining_result_capacity(state),
        )
        offer_slots = store.remaining_offer_slots(state)
        chosen = []
        for action in ranked:
            if len(chosen) >= budget:
                break
            if isinstance(action, actions.Offer):
                if offer_slots <= 0:
                    continue
                offer_slots -= 1
            chosen.append(action)
        return chosen

    # --- shared helpers --------------------------------------------------
    def _order(self, state: model.State) -> list[model.Resource]:
        """Resource order from the rules, which also breaks ties."""
        return list(state.rules.resource_order) or list(model.Resource)

    def cover(self, state: model.State) -> dict[model.Resource, float]:
        """Ticks of cover per resource: stock divided by upkeep.

        A resource with no upkeep is never consumed, so it lasts forever and
        can never be in danger.

        Production is deliberately left out.  How much a station makes can
        differ from run to run and tick to tick, so a rate sampled from one
        tick is not something to project forward -- the stock actually on
        hand is the one number this can trust.
        """
        observation = state.observation
        ticks = {}
        for resource in self._order(state):
            upkeep = observation.upkeep_per_tick.get(resource)
            if upkeep <= 0:
                ticks[resource] = math.inf
            else:
                ticks[resource] = observation.inventory.get(resource) / upkeep
        return ticks

    def value(self, prices, bundle: model.Bundle) -> float:
        """What a bundle is worth to us, in our own prices."""
        return sum(prices[resource] * bundle.get(resource) for resource in prices)

    def _affordable(self, state, paid: model.Bundle) -> bool:
        inventory = state.observation.inventory
        return all(inventory.get(r) >= paid.get(r) for r in self._order(state))

    def _expiry(self, state, ttl_ticks: int, ceiling_rule: str) -> int:
        """An expiry that is in the future and inside the run's ceiling."""
        ceiling = getattr(state.rules, ceiling_rule)
        return state.tick + max(1, min(ttl_ticks, ceiling))
