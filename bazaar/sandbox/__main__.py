"""Run a local Bazaar game with bot traders.

    python -m bazaar.sandbox [--port 3002] [--tick-ms 1000] [--ticks 120]
                             [--start-after 3] [--seed 7] [--station P08]

Then, in another terminal, point any client at it:

    BAZAAR_TOKEN=sandbox python -m bazaar --policy jesus --url ws://127.0.0.1:3002/ws

and, if the dashboard (``bazaar-dashboard/``) is set up and started, watch it
there.  Any token is accepted; ``sandbox-P03``
plays station P03, so two clients can play against each other while bots play
the rest.  Setting the token explicitly also keeps a live token from ``.env``
from ever being sent here.

The run waits in PHASE_READY until a client declares ready, then starts.  When
it finishes, the server stays up so the final state can be read; stop it with
Ctrl+C.  Restarting creates a fresh run.
"""

import argparse
import asyncio
import sys
from dataclasses import replace

from .. import config
from .server import SandboxServer
from .world import World


def parse_args(argv=None) -> argparse.Namespace:
    d = config.SANDBOX
    parser = argparse.ArgumentParser(prog="python -m bazaar.sandbox", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=d.host)
    parser.add_argument("--port", type=int, default=d.port)
    parser.add_argument("--tick-ms", type=int, default=d.tick_duration_ms, help=f"tick length (default {d.tick_duration_ms}; the live game has used 1000-10000)")
    parser.add_argument("--ticks", type=int, default=d.duration_ticks, help=f"run length in ticks (default {d.duration_ticks})")
    parser.add_argument("--start-after", type=float, default=d.start_after_seconds, help="seconds in PHASE_READY after the first client is ready")
    parser.add_argument("--seed", type=int, default=d.seed, help="bot behaviour; the same seed replays the same market")
    parser.add_argument("--station", default=d.station, help="station a client plays unless its token names one")
    parser.add_argument("--stations", type=int, default=d.stations)
    return parser.parse_args(argv)


def _progress(world: World) -> None:
    """One line per tick for each station a client is playing."""
    for station in world.stations.values():
        if station.claimed:
            water, food, components = station.inventory.values()
            print(
                f"  tick {world.tick:3d}/{world.rules.duration_ticks}  {station.station_id}: "
                f"water={water} food={food} components={components} health={station.health}"
                + ("  FAILED" if station.failed else "")
            )
    failed = sum(s.failed for s in world.stations.values())
    if world.tick % 10 == 0:
        print(f"  -- market: {len(world.transactions)} trades settled, {failed} station(s) failed")


async def main_async(args: argparse.Namespace) -> None:
    settings = replace(
        config.SANDBOX,
        host=args.host, port=args.port, tick_duration_ms=args.tick_ms, duration_ticks=args.ticks,
        start_after_seconds=args.start_after, seed=args.seed, station=args.station, stations=args.stations,
    )
    world = World(settings)
    if settings.station not in world.stations:
        sys.exit(f"--station {settings.station} is not one of {', '.join(world.stations)}")
    server = SandboxServer(world, on_tick=_progress)
    try:
        url = await server.start(settings.host, settings.port)
    except OSError as exc:
        # A finished sandbox keeps serving its final state, so the likeliest
        # holder of the port is the previous one.
        sys.exit(f"cannot listen on {settings.host}:{settings.port} ({exc.strerror}); "
                 "is another sandbox still running? Stop it with Ctrl+C, or pass --port.")
    print(f"sandbox: {url}  run {world.run_id}, {settings.stations} stations, "
          f"{settings.duration_ticks} ticks of {settings.tick_duration_ms} ms")
    print(f"connect: BAZAAR_TOKEN=sandbox python -m bazaar --policy jesus --url {url}")
    print(f"         (token 'sandbox-P03' plays P03; any other token plays {settings.station})")
    print("waiting for a client to declare ready ...")
    try:
        await server.finished.wait()
        outcome = world.state_for(settings.station, 0).outcome
        print(f"run finished: collective_success={outcome.collective_success}; "
              "still serving the final state, Ctrl+C to stop")
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main(argv=None) -> int:
    # Progress lines should appear as they happen, even when piped to a file.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(main_async(parse_args(argv)))
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
