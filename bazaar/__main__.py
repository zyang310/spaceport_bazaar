"""Command line entry point.

    python -m bazaar --policy scripted|utility [--shadow utility]
                     [--capture-fixtures] [--url URL] [--max-seconds N]

The token comes from ``$BAZAAR_TOKEN`` or a gitignored ``.env``, falling back
to a practice-server credentials file.  It is never printed; only its source
is.

``--policy utility`` runs with no time limit by default, so it stays connected
while a real game sits in ``PHASE_READY`` awaiting the instructor.  Stop it
with Ctrl+C, or bound it with ``--max-seconds``.  The scripted exercise always
ends on its own.

Exit code 0 when every check passed; nonzero on a failed check, an unexpected
protocol error, or a session the server closed on us; 130 on Ctrl+C.
"""

import argparse
import asyncio
import sys
import time

from . import config, runner
from .brain.policy.scripted import ScriptedPolicy
from .brain.policy.utility import UtilityPolicy
from .network.transport import BazaarClient, ConnectionFailed, resolve_token
from .runlog import RunLog


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m bazaar", description=__doc__)
    parser.add_argument(
        "--policy",
        choices=("scripted", "utility"),
        default="scripted",
        help="scripted replays the guide's exercise; utility runs the agent",
    )
    parser.add_argument(
        "--shadow",
        choices=("utility",),
        default=None,
        help="run this policy on every state and log what it would do, without sending it",
    )
    parser.add_argument(
        "--capture-fixtures",
        action="store_true",
        help="write every received frame to tests/fixtures/ as raw bytes",
    )
    parser.add_argument(
        "--url",
        default=config.DEFAULT_URL,
        help=f"default {config.DEFAULT_URL}; the bundled practice server is {config.PRACTICE_URL}",
    )
    parser.add_argument("--credentials", default=str(config.CREDENTIALS_PATH))
    parser.add_argument("--station", default=config.STATION_ID)
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help=(
            "time limit for --policy utility; unlimited by default, so it stays "
            "connected through PHASE_READY until Ctrl+C (the scripted run always "
            "ends on its own)"
        ),
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    token, source = resolve_token(args.credentials, args.station)
    print(f"token source: {source}")

    # The run ID is only known once the first state arrives, so logs start in a
    # timestamped directory and the real run ID is recorded inside them.
    run_dir = config.RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
    run_log = RunLog(run_dir)
    print(f"logging to {run_dir}")

    shadow = None
    if args.shadow == "utility":
        shadow = runner.ShadowRecorder(UtilityPolicy(), run_log)
    capture = runner.FixtureCapture(config.ROOT / "tests" / "fixtures") if args.capture_fixtures else None

    outcome = runner.RunOutcome()
    try:
        async with BazaarClient(
            args.url,
            token,
            run_log=run_log,
            on_state=shadow,
            on_frame=capture,
        ) as client:
            print(f"connected to {args.url} as {args.station}")
            if args.policy == "scripted":
                policy = ScriptedPolicy()
                await runner.run_scripted(client, policy, outcome)
            else:
                policy = UtilityPolicy()
                await runner.run_utility(client, policy, run_log, outcome, args.max_seconds)

            run_log.write(
                "summary.json",
                {
                    "policy": args.policy,
                    "run_id": client.state.run_id if client.state else None,
                    "sent": client.sent,
                    "received": client.received,
                    "failures": outcome.failures,
                },
            )
            print(f"messages: sent {client.sent}, received {client.received}")
            for error in client.unmatched_errors:
                print(f"  unmatched protocol_error: {error.code.name}")
                if error.close_session:
                    outcome.fail(f"server closed the session with {error.code.name}")
    except ConnectionFailed as exc:
        outcome.fail(f"connection failed: {exc}")
    except Exception as exc:
        outcome.fail(f"{type(exc).__name__}: {exc}")

    if shadow is not None:
        print(f"shadow: {shadow.states} states seen, {shadow.proposed} actions proposed, 0 sent")
    if capture is not None:
        print(f"fixtures: {capture.count} frames written to tests/fixtures/")

    if outcome.ok:
        print("RESULT: ok")
        return 0
    print(f"RESULT: {len(outcome.failures)} failure(s)")
    for failure in outcome.failures:
        print(f"  - {failure}")
    return 1


def main(argv=None) -> int:
    try:
        return asyncio.run(main_async(parse_args(argv)))
    except KeyboardInterrupt:
        # The expected way to stop an unlimited --policy utility run.
        print("\ninterrupted")
        return 130  # shell convention: 128 + SIGINT


if __name__ == "__main__":
    sys.exit(main())
