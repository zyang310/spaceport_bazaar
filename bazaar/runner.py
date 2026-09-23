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
    client, policy, run_log, outcome: RunOutcome, max_seconds: float | None
) -> RunOutcome:
    """Feed the agent states and carry out what it decides.

    The agent decides at most once per ``world_version``, so an extra snapshot
    of unchanged state -- a sync, say -- never triggers the same actions twice.

    ``max_seconds`` is ``None`` by default, meaning no limit: a real game may
    sit in ``PHASE_READY`` for a while before an instructor starts it, and the
    agent should stay connected and waiting rather than give up. Stop an
    unlimited run with Ctrl+C, or pass ``--max-seconds`` for a bounded one.
    """
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

    while not expired():
        state = client.state
        if state is None:
            break
        if state.world_version in decided_versions:
            # Nothing new to think about; wait for the next snapshot.
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

        for action in chosen:
            if expired():
                break
            issued += 1
            request_id = f"utility-{issued:04d}"
            message = action.to_message(builder, request_id)
            try:
                raw = limits.validate_command(message, state)
            except limits.CommandRejected as exc:
                outcome.fail(f"agent produced an illegal command: {exc}")
                print(f"    REJECTED {action.describe()} -- {exc}")
                continue

            print(f"    {action.describe()}  <- {action.reason}")
            try:
                result = await _send_with_one_retry(client, message, raw, request_id)
            except ProtocolErrorReceived as exc:
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
                outcome.fail(f"{request_id} got no answer: {type(exc).__name__}: {exc}")
                return outcome

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
                await client.wait_for_version(result.processed_version, timeout=5)
            except asyncio.TimeoutError:
                pass

    outcome.commands_sent = client.sent
    return outcome


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
