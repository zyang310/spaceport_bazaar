"""The guide's ten-step exercise, replayed exactly.

The practice server accepts only this sequence: a new command out of order ends
the exercise with a ``scenario mismatch`` report.  So this policy exists to
prove the shared machinery -- connection, builders, validator, state tracking --
against a live server, not to decide anything.

Every expected value below comes from the starter README, and the per-step
checks are ported from the standalone ``network/walkthrough.py`` diagnostic.
Steps 5 and 6 send nothing at all; the server pushes those states on its own.
"""

from ...validation import model
from ...validation.commands import CommandBuilder
from ...network.transport import ProtocolErrorReceived

WATER = model.Resource.RESOURCE_WATER
FOOD = model.Resource.RESOURCE_FOOD
COMPONENTS = model.Resource.RESOURCE_COMPONENTS

#: The guide's own request IDs.  Reusing an ID means retrying that exact
#: command, so these are fixed rather than generated.
ADVERTISE_1 = "student-advertise-1"
ADVERTISE_SEEKING_1 = "student-advertise-seeking-1"
OFFER_1 = "student-offer-1"
ACCEPT_1 = "student-accept-1"
WITHDRAW_1 = "student-withdraw-1"
ADVERTISE_2 = "student-advertise-2"

#: The guide's expiry for every advertisement and offer in the exercise.
EXPIRES_TICK = 6


class CheckFailed(AssertionError):
    """One or more of the guide's expected values did not hold."""


class Checks:
    """Records each expected value as it is checked, and prints it.

    Failures are collected rather than raised immediately: one mismatch early
    on usually explains several later ones, and seeing all of them together is
    what makes the cause obvious.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.passed = 0
        self.failures: list[str] = []

    def __call__(self, label: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            if self.verbose:
                print(f"    [pass] {label}")
        else:
            message = f"{label}" + (f"  ({detail})" if detail else "")
            self.failures.append(message)
            print(f"    [FAIL] {message}")
        return bool(condition)

    @property
    def ok(self) -> bool:
        return not self.failures

    def raise_if_failed(self) -> None:
        if self.failures:
            raise CheckFailed(
                f"{len(self.failures)} check(s) failed:\n  - " + "\n  - ".join(self.failures)
            )


def inventory_of(state: model.State) -> tuple[int, int, int]:
    return state.observation.inventory.as_tuple()


class ScriptedPolicy:
    """Drives the guide's exercise over a live connection."""

    name = "scripted"

    def __init__(self, verbose: bool = True):
        self.checks = Checks(verbose=verbose)
        self.verbose = verbose
        self.advertisement_id: str | None = None
        self.zero_price_offer_id: str | None = None
        self.last_completed_step = 0

    def _step(self, number: int, title: str) -> None:
        self.last_completed_step = number - 1
        if self.verbose:
            print(f"  step {number}: {title}")

    def _check_state(self, state, *, world: int, sequence: int, inventory, transactions=None):
        check = self.checks
        check(
            f"state world_version={world} snapshot_sequence={sequence}",
            (state.world_version, state.snapshot_sequence) == (world, sequence),
            f"got world_version={state.world_version} snapshot_sequence={state.snapshot_sequence}",
        )
        check(f"inventory {inventory}", inventory_of(state) == inventory, f"got {inventory_of(state)}")
        check(
            "tick 0 and PHASE_RUNNING",
            state.tick == 0 and state.phase is model.Phase.PHASE_RUNNING,
            f"got tick={state.tick} phase={state.phase.name}",
        )
        if transactions is not None:
            check(
                f"{transactions} transaction(s)",
                len(state.transactions) == transactions,
                f"got {len(state.transactions)}",
            )

    def _check_result(self, result, request_id: str) -> None:
        self.checks(
            f"result {request_id}: ok and RESULT_CODE_OK",
            result.request_id == request_id
            and result.ok
            and result.code is model.ResultCode.RESULT_CODE_OK,
            f"got request_id={result.request_id} ok={result.ok} code={result.code.name}",
        )

    async def run(self, client) -> None:
        """Send the exercise, checking every value the guide lists."""
        check = self.checks

        # --- 1. starting state and readiness ------------------------------
        self._step(1, "read the starting state and confirm readiness")
        state = await client.first_state()
        builder = CommandBuilder(state.run_id)
        self._check_state(state, world=2, sequence=1, inventory=(30, 30, 30))
        check(
            "station P01 with RESOURCE_WATER specialty",
            state.self_station_id == "P01" and state.observation.specialty is WATER,
            f"got {state.self_station_id} / {state.observation.specialty.name}",
        )
        peer = [a for a in state.advertisements if a.station_id == "P02"]
        check(
            "P02 advertises food and seeks water",
            len(peer) == 1 and peer[0].selling == [FOOD] and peer[0].seeking == [WATER],
            f"got {[(a.selling, a.seeking) for a in peer]}",
        )
        check(
            "rules store five command results",
            state.rules.max_request_records_per_station == 5,
            f"got {state.rules.max_request_records_per_station}",
        )
        readiness = await client.declare_ready(state.snapshot_sequence)
        check(
            "readiness echoes ready=true and snapshot_sequence=1",
            readiness.ready
            and readiness.run_id == state.run_id
            and readiness.snapshot_sequence == 1,
            f"got ready={readiness.ready} snapshot_sequence={readiness.snapshot_sequence}",
        )

        # --- 2. advertise water for food ----------------------------------
        self._step(2, "advertise water for food")
        result = await client.request(
            builder.advertise(ADVERTISE_1, [WATER], [FOOD], EXPIRES_TICK), ADVERTISE_1
        )
        self._check_result(result, ADVERTISE_1)
        state = await client.state_with_sequence(2)
        self._check_state(state, world=3, sequence=2, inventory=(30, 30, 30))
        check(
            "our advertisement appears",
            any(a.station_id == "P01" for a in state.advertisements),
        )

        # --- 3. replace it with a request for components -------------------
        self._step(3, "replace the advertisement with a request for components")
        result = await client.request(
            builder.advertise(ADVERTISE_SEEKING_1, [], [COMPONENTS], EXPIRES_TICK),
            ADVERTISE_SEEKING_1,
        )
        self._check_result(result, ADVERTISE_SEEKING_1)
        check("result carries an object_id", result.object_id is not None, f"got {result.object_id}")
        self.advertisement_id = result.object_id
        state = await client.state_with_sequence(3)
        self._check_state(state, world=4, sequence=3, inventory=(30, 30, 30))
        active = [
            a
            for a in state.advertisements
            if a.station_id == "P01"
            and a.status is model.PublicationStatus.PUBLICATION_STATUS_ACTIVE
        ]
        check(
            "one active listing: sells nothing, seeks components",
            len(active) == 1 and active[0].selling == [] and active[0].seeking == [COMPONENTS],
            f"got {[(a.selling, a.seeking) for a in active]}",
        )

        # --- 4. offer two water for one food -------------------------------
        self._step(4, "offer two water for one food")
        result = await client.request(
            builder.offer(
                OFFER_1, "P02", model.Bundle(water=2), model.Bundle(food=1), EXPIRES_TICK
            ),
            OFFER_1,
        )
        self._check_result(result, OFFER_1)
        state = await client.state_with_sequence(4)
        self._check_state(state, world=5, sequence=4, inventory=(30, 30, 30))
        ours = [o for o in state.offers if o.proposer_id == "P01"]
        check(
            "our offer to P02 is OPEN",
            len(ours) == 1 and ours[0].status is model.OfferStatus.OFFER_STATUS_OPEN,
            f"got {[o.status.name for o in ours]}",
        )

        # --- 5. P02 accepts, unprompted -------------------------------------
        self._step(5, "observe P02 accept the offer")
        state = await client.state_with_sequence(5)
        self._check_state(state, world=6, sequence=5, inventory=(28, 31, 30), transactions=1)
        check(
            "our offer is ACCEPTED",
            [o.status for o in state.offers if o.proposer_id == "P01"]
            == [model.OfferStatus.OFFER_STATUS_ACCEPTED],
        )

        # --- 6. P02 sends a gift --------------------------------------------
        self._step(6, "observe P02 offer a gift")
        state = await client.state_with_sequence(6)
        self._check_state(state, world=7, sequence=6, inventory=(28, 31, 30))
        gifts = [
            o
            for o in state.offers
            if o.proposer_id == "P02" and o.status is model.OfferStatus.OFFER_STATUS_OPEN
        ]
        check(
            "an open P02 gift: gives one component, asks nothing",
            len(gifts) == 1
            and gifts[0].give.as_tuple() == (0, 0, 1)
            and gifts[0].receive.is_zero(),
            f"got {[(o.give.as_tuple(), o.receive.as_tuple()) for o in gifts]}",
        )
        if gifts:
            self.zero_price_offer_id = gifts[0].offer_id

        # --- 7. accept the gift ----------------------------------------------
        self._step(7, "accept the gift")
        result = await client.request(
            builder.accept(ACCEPT_1, self.zero_price_offer_id), ACCEPT_1
        )
        self._check_result(result, ACCEPT_1)
        check(
            "result identifies the transaction",
            result.transaction_id is not None,
            f"got {result.transaction_id}",
        )
        state = await client.state_with_sequence(7)
        self._check_state(state, world=8, sequence=7, inventory=(28, 31, 31), transactions=2)
        check(
            "the listing seeking components is still active",
            any(
                a.advertisement_id == self.advertisement_id
                and a.status is model.PublicationStatus.PUBLICATION_STATUS_ACTIVE
                for a in state.advertisements
            ),
        )

        # --- 8. remove the advertisement --------------------------------------
        self._step(8, "remove the advertisement")
        result = await client.request(
            builder.withdraw(WITHDRAW_1, self.advertisement_id), WITHDRAW_1
        )
        self._check_result(result, WITHDRAW_1)
        state = await client.state_with_sequence(8)
        self._check_state(state, world=9, sequence=8, inventory=(28, 31, 31), transactions=2)
        check(
            "the advertisement is no longer active",
            not any(
                a.advertisement_id == self.advertisement_id
                and a.status is model.PublicationStatus.PUBLICATION_STATUS_ACTIVE
                for a in state.advertisements
            ),
        )

        # --- 9. the intentional request-limit error ----------------------------
        self._step(9, "observe the intentional request-limit error")
        # All five stored result slots are used, so this command is refused.
        # That refusal is the expected outcome, not a failure, and it has no
        # stored result -- so it is never retried.
        try:
            result = await client.request(
                builder.advertise(ADVERTISE_2, [WATER], [FOOD], EXPIRES_TICK), ADVERTISE_2
            )
        except ProtocolErrorReceived as raised:
            error = raised.error
            check(
                "CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED with close_session=false",
                error.code is model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
                and not error.close_session,
                f"got {error.code.name} close_session={error.close_session}",
            )
            check(
                f"the error names {ADVERTISE_2}",
                error.request_id == ADVERTISE_2,
                f"got {error.request_id}",
            )
        else:
            check(
                "step 9 is refused rather than accepted",
                False,
                f"got a result: ok={result.ok} code={result.code.name}",
            )

        # --- 10. ask for the final state ----------------------------------------
        self._step(10, "ask for the final state")
        # The first sync of the exercise: an earlier one would shift every
        # snapshot_sequence the guide lists.
        state = await client.sync()
        self._check_state(state, world=9, sequence=9, inventory=(28, 31, 31), transactions=2)
        check(
            "five stored command results",
            len(state.request_results) == 5,
            f"got {len(state.request_results)}",
        )
        observation = state.observation
        check(
            "imported (0,1,1) and exported (2,0,0)",
            observation.imported_total.as_tuple() == (0, 1, 1)
            and observation.exported_total.as_tuple() == (2, 0, 0),
            f"got imported={observation.imported_total.as_tuple()} "
            f"exported={observation.exported_total.as_tuple()}",
        )
        check(
            "production and shortage counters are zero, since no tick occurred",
            observation.produced_total.is_zero()
            and observation.consumed_total.is_zero()
            and observation.shortage_ticks == 0,
        )
        self.last_completed_step = 10

        # --- the guide's message counts ------------------------------------------
        extra = await client.quiet()
        check("no unexpected extra state followed", extra is None, f"got {extra and extra.snapshot_sequence}")
        check(
            "sent 8 and received 16 messages",
            (client.sent, client.received) == (8, 16),
            f"got sent={client.sent} received={client.received}",
        )
