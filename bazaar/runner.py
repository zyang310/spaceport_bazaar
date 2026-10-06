"""The loop: state in, policy, validator, socket, result, repeat.

Two loops live here because the two policies work differently.  The scripted
exercise drives the connection itself, step by step.  The utility agent is a
pure function of state, so the loop feeds it states and carries out what it
returns.

Everything the agent decides is logged with its reason, whether or not it was
sent, which is what makes a shadow run worth reading afterwards.
"""

import asyncio
from pathlib import Path

from . import config
from .brain.policy.scripted import CheckFailed
from .network.transport import ProtocolErrorReceived
from .validation import limits, model
from .validation.commands import CommandBuilder


class FixtureCapture:
    """Writes every received frame to ``tests/fixtures/<NN>_<kind>.bin``.

    Raw bytes, exactly as the server sent them, so a test can decode real
    states rather than ones we built to suit ourselves.  Tokens live only in
    connection headers and never appear in a message, so these are safe to
    commit.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def __call__(self, raw: bytes, message: model.ServerMessage) -> None:
        self.count += 1
        path = self.directory / f"{self.count:02d}_{message.which()}.bin"
        path.write_bytes(raw)


class ShadowRecorder:
    """Runs a policy on every state and logs what it would have done.

    Nothing here is ever sent.  The point is to watch the agent think while the
    scripted exercise drives the connection, and to prove it copes with real
    states before it is trusted with one.
    """

    def __init__(self, policy, run_log):
        self.policy = policy
        self.run_log = run_log
        self.states = 0
        self.proposed = 0

    def __call__(self, state: model.State) -> None:
        self.states += 1
        try:
            decided = self.policy.decide(state)
            record = {
                "snapshot_sequence": state.snapshot_sequence,
                "world_version": state.world_version,
                "sent": False,
                "actions": [
                    {"action": action.describe(), "reason": action.reason, "score": action.score}
                    for action in decided
                ],
            }
            self.proposed += len(decided)
        except Exception as exc:  # a shadow must never break the real run
            record = {
                "snapshot_sequence": state.snapshot_sequence,
                "world_version": state.world_version,
                "sent": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        self.run_log.write("shadow.jsonl", record)


def fan_out(*callbacks):
    """One ``on_state`` callback that calls each of several in turn.

    ``BazaarClient`` takes a single callback, and a run may want a shadow and
    other observers watching at once.  ``None`` entries are dropped, so optional
    observers can be passed unconditionally.
    """
    live = [callback for callback in callbacks if callback is not None]
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def call_all(state: model.State) -> None:
        for callback in live:
            callback(state)

    return call_all


class _NoActivity:
    """Stands in for a reporter when nobody is watching, so the loop needs no ``if``s."""

    def decided(self, state, actions) -> None: ...
    def sent(self, action, request_id) -> None: ...
    def resolved(self, request_id, result) -> None: ...
    def rejected(self, action, reason) -> None: ...
    def errored(self, request_id, message) -> None: ...


class RunOutcome:
    """What happened, in the form the CLI turns into an exit code."""

    def __init__(self):
        self.failures: list[str] = []
        self.commands_sent = 0
        self.protocol_errors: list[model.ProtocolError] = []

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, message: str) -> None:
        self.failures.append(message)


async def run_scripted(client, policy, outcome: RunOutcome) -> RunOutcome:
    """Replay the guide's exercise and report every check it makes."""
    try:
        await policy.run(client)
    except CheckFailed as exc:
        outcome.fail(str(exc))
    except ProtocolErrorReceived as exc:
        outcome.protocol_errors.append(exc.error)
        outcome.fail(f"unexpected protocol error: {exc}")
    if policy.checks.failures:
        for failure in policy.checks.failures:
            outcome.fail(failure)
    outcome.commands_sent = client.sent
    print(
        f"  checks: {policy.checks.passed} passed, {len(policy.checks.failures)} failed; "
        f"reached step {policy.last_completed_step}"
    )
    return outcome


async def run_utility(
    client,
    policy,
    run_log,
    outcome: RunOutcome,
    max_seconds: float | None,
    activity=None,
    *,
    session: str,
) -> RunOutcome:
    """Feed the agent states and carry out what it decides.

    The agent decides at most once per ``world_version``, so an extra snapshot
    of unchanged state -- a sync, say -- never triggers the same actions twice.

    A decision is only good for the tick it was made in.  Commands go out one
    at a time, each waiting for its result, so a batch can outlive its tick --
    and then its expiries are already due, the offers it accepts may have
    closed, and upkeep has eaten into the inventory it was priced on.  So once
    the tick moves on, whatever is left of the batch is dropped and the agent
    decides afresh.  Nothing worth doing is lost: the agent is pure, so
    anything still worth doing is simply chosen again.

    A policy's own budget only holds within one call to ``decide``: it caps
    that decision at ``new_commands_per_station_per_tick``, but cannot know
    how many commands an *earlier* decision already sent this same tick --
    and our own accepts and offers settle immediately, each pushing a new
    state that triggers another decision long before the tick itself moves
    on.  So this loop keeps its own count of commands sent since the tick
    began, resets it when the tick changes, and stops sending -- not
    deciding, just sending -- once the run's limit is reached, whatever any
    single decision asked for.  If the server still answers
    ``RESULT_CODE_RATE_LIMITED`` (another session on the same station, say),
    that count is taken as the truth and clamped to the limit rather than
    argued with.

    Waiting for one command's result, and then for our own view to catch up
    with it, are both timed in ticks (``config.COMMAND_TIMEOUT_TICKS`` and
    ``config.VIEW_CATCHUP_TIMEOUT_TICKS``) rather than a fixed number of
    seconds, and converted at the point of use via ``_ticks_to_seconds``. A
    fixed-second timeout is harmless at the live game's slowest cadence but,
    at its fastest, blocks the loop for many ticks over one slow reply.

    ``max_seconds`` is ``None`` by default, meaning no limit: a real game may
    sit in ``PHASE_READY`` for a while before an instructor starts it, and the
    agent should stay connected and waiting rather than give up. Stop an
    unlimited run with Ctrl+C, or pass ``--max-seconds`` for a bounded one.

    ``activity`` is told each action's progress -- decided, sent, answered --
    which a state never reports.  ``report.DashboardReporter`` is the one in
    practice, when the external dashboard's tap is installed.

    ``session`` goes into every request ID.  The server remembers IDs for the
    whole run, not the connection, so a client restarted mid-game that counted
    from 1 again would have every command refused as a
    ``RESULT_CODE_REQUEST_ID_CONFLICT``.  One live run lost all 343 that way.
    """
    activity = activity or _NoActivity()
    loop = asyncio.get_running_loop()
    deadline = None if max_seconds is None else loop.time() + max_seconds

    def expired() -> bool:
        return deadline is not None and loop.time() >= deadline

    def poll_seconds() -> float:
        """How long to wait for the next state before rechecking.

        Unlimited runs still poll in short bursts rather than blocking
        forever, so the loop notices a closed connection promptly.
        """
        if deadline is None:
            return 1.0
        return min(1.0, max(0.05, deadline - loop.time()))

    state = await client.first_state()
    await client.declare_ready(state.snapshot_sequence)
    builder = CommandBuilder(state.run_id)

    decided_versions: set[int] = set()
    issued = 0
    tracked_tick: int | None = None
    commands_sent_this_tick = 0

    while not expired():
        state = client.state
        if state is None:
            break
        if state.tick != tracked_tick:
            tracked_tick = state.tick
            commands_sent_this_tick = 0
        budget = state.rules.new_commands_per_station_per_tick
        if state.world_version in decided_versions:
            # Nothing new to think about; wait for the next snapshot.
            if await client.quiet(seconds=poll_seconds()) is None:
                continue
            continue
        if commands_sent_this_tick >= budget:
            # An earlier decision already spent this tick's whole budget;
            # nothing more can go out until the tick advances.  Not marked
            # decided: once it does advance, this same version (if it is
            # still the latest) is worth deciding on properly.
            if await client.quiet(seconds=poll_seconds()) is None:
                continue
            continue

        decided_versions.add(state.world_version)
        water, food, components = state.observation.inventory.as_tuple()
        print(
            f"  v{state.world_version} (tick {state.tick}, {state.phase.name}): "
            f"water={water} food={food} components={components}"
        )
        chosen = policy.decide(state)
        activity.decided(state, chosen)
        run_log.write(
            "decisions.jsonl",
            {
                "snapshot_sequence": state.snapshot_sequence,
                "world_version": state.world_version,
                "inventory": state.observation.inventory.as_tuple(),
                "actions": [
                    {"action": a.describe(), "reason": a.reason, "score": a.score} for a in chosen
                ],
            },
        )
        if not chosen:
            print("    no action")
            if await client.quiet(seconds=poll_seconds()) is None:
                continue
            continue

        for index, action in enumerate(chosen):
            if expired():
                break
            latest = client.state
            if latest is not None and latest.tick != state.tick:
                dropped = len(chosen) - index
                print(
                    f"    tick {state.tick} -> {latest.tick}: dropping {dropped} "
                    "stale action(s) to decide again"
                )
                run_log.write(
                    "decisions.jsonl",
                    {
                        "world_version": state.world_version,
                        "stale_after_tick": state.tick,
                        "now_tick": latest.tick,
                        "dropped": [a.describe() for a in chosen[index:]],
                    },
                )
                break
            if commands_sent_this_tick >= budget:
                # An earlier decision already spent this tick's whole budget;
                # the rest waits for the next one, same as a stale batch.
                dropped = len(chosen) - index
                print(
                    f"    tick {state.tick}: command budget of {budget} already spent "
                    f"this tick; dropping {dropped} action(s)"
                )
                run_log.write(
                    "decisions.jsonl",
                    {
                        "world_version": state.world_version,
                        "budget_exhausted_at_tick": state.tick,
                        "dropped": [a.describe() for a in chosen[index:]],
                    },
                )
                break
            issued += 1
            request_id = f"utility-{session}-{issued:04d}"
            message = action.to_message(builder, request_id)
            try:
                raw = limits.validate_command(message, state)
            except limits.CommandRejected as exc:
                outcome.fail(f"agent produced an illegal command: {exc}")
                print(f"    REJECTED {action.describe()} -- {exc}")
                activity.rejected(action, str(exc))
                continue

            print(f"    {action.describe()}  <- {action.reason}")
            activity.sent(action, request_id)
            # Counted now, not on a successful result: a retry reuses this
            # same request ID, so the server never charges the tick twice for
            # it even if this attempt times out or fails.
            commands_sent_this_tick += 1
            try:
                result = await _send_with_one_retry(
                    client, message, raw, request_id,
                    timeout=_ticks_to_seconds(state, config.COMMAND_TIMEOUT_TICKS),
                )
            except ProtocolErrorReceived as exc:
                activity.errored(request_id, exc.error.code.name)
                outcome.protocol_errors.append(exc.error)
                run_log.write(
                    "decisions.jsonl",
                    {"request_id": request_id, "protocol_error": exc.error.code.name},
                )
                print(f"    -> protocol_error {exc.error.code.name}")
                if exc.error.code is model.ControlCode.CONTROL_CODE_BAD_MESSAGE:
                    outcome.fail(f"{request_id} was rejected as a bad message")
                return outcome  # the run cannot continue meaningfully
            except Exception as exc:
                activity.errored(request_id, f"{type(exc).__name__}: {exc}")
                outcome.fail(f"{request_id} got no answer: {type(exc).__name__}: {exc}")
                return outcome

            activity.resolved(request_id, result)
            if result.code is model.ResultCode.RESULT_CODE_RATE_LIMITED:
                # Our own count said there was room; the server disagrees --
                # perhaps a previous session already spent part of this tick's
                # budget before we did.  Its count is the truth: stop sending
                # until the tick advances, rather than argue with it.
                commands_sent_this_tick = budget
            run_log.write(
                "decisions.jsonl",
                {
                    "request_id": request_id,
                    "ok": result.ok,
                    "code": result.code.name,
                    "processed_version": result.processed_version,
                },
            )
            print(f"    -> {result.code.name} (ok={result.ok})")
            # Act on a view that already includes what we just did.
            try:
                await client.wait_for_version(
                    result.processed_version,
                    timeout=_ticks_to_seconds(state, config.VIEW_CATCHUP_TIMEOUT_TICKS),
                )
            except asyncio.TimeoutError:
                pass

    outcome.commands_sent = client.sent
    return outcome


def _ticks_to_seconds(state: model.State, ticks: float) -> float:
    """A timeout expressed in ticks, converted to the seconds ``asyncio`` wants.

    ``state.rules.tick_duration_ms`` is the run's own cadence, so a timeout set
    this way costs the same number of ticks whether the game runs at 1 second
    or 10 seconds a tick.  Floored well above zero so a degenerate tick length
    cannot produce a timeout that fires before the request is even sent.
    """
    return max(0.05, state.rules.tick_duration_ms / 1000 * ticks)


async def _send_with_one_retry(client, message, raw, request_id: str, timeout: float = 10):
    """Await the result, retrying the identical command once on a timeout.

    Reusing the request ID is what makes this a retry rather than a new
    command: the server returns the stored result instead of acting twice.
    """
    try:
        return await client.request(message, request_id, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"    (timeout; retrying {request_id} unchanged)")
        return await client.request(message, request_id, timeout=timeout)
