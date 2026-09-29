"""The sandbox's socket: the live game's handshake and message flow, locally.

What a client sees here matches the guide:

* HTTP 401 without a bearer token, HTTP 400 without the ``bazaar.protobuf.v2``
  subprotocol.  Any token is accepted; ``sandbox-P03`` plays P03, anything
  else plays the default station.
* A ``state`` as soon as the connection opens, ``snapshot_sequence`` counting
  from 1 on every connection.
* Readiness per connection.  Trading before it is ``BAD_MESSAGE``, with the
  session kept open.
* A ``result``, then a ``state``, for each command.  States also arrive
  unasked, whenever the world moves.
* A second connection for the same station fences the first.

The run sits in ``PHASE_READY`` until a client declares ready, then starts a few
seconds later, the way an instructor would start a class game.
"""

import asyncio
import random

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .. import config
from ..validation import decode, encode, model
from .bots import bots_act
from .world import PROTOCOL_VERSION, World, protocol_error

SUBPROTOCOL = "bazaar.protobuf.v2"
TOKEN_PREFIX = "sandbox-"


class _Session:
    """One connection, and the counters the protocol keeps per connection."""

    def __init__(self, connection, station_id: str):
        self.connection = connection
        self.station_id = station_id
        self.sequence = 0
        self.ready = False


class SandboxServer:
    """Serves one :class:`World` and drives its clock."""

    def __init__(self, world: World, *, start_after: float | None = None, on_tick=None):
        self.world = world
        self.settings = world.settings
        if self.settings.station not in world.stations:
            raise ValueError(f"default station {self.settings.station} is not one of {', '.join(world.stations)}")
        self.start_after = self.settings.start_after_seconds if start_after is None else start_after
        #: Called after every tick with the world, e.g. to print progress.
        self.on_tick = on_tick
        self.rng = random.Random(self.settings.seed)
        self.sessions: dict[str, _Session] = {}
        self.finished = asyncio.Event()
        self._first_ready = asyncio.Event()
        self._server = None
        self._clock: asyncio.Task | None = None

    # --- lifecycle ------------------------------------------------------------
    async def start(self, host: str, port: int) -> str:
        self._server = await serve(
            self._handler, host, port,
            process_request=self._route,
            subprotocols=[SUBPROTOCOL],
            max_size=self.settings.max_command_bytes * 2,
        )
        self._clock = asyncio.create_task(self._run_clock(), name="sandbox-clock")
        return self.url

    async def stop(self) -> None:
        if self._clock is not None:
            self._clock.cancel()
            try:
                await self._clock
            except asyncio.CancelledError:
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        host = self._server.sockets[0].getsockname()[0]
        return f"ws://{host}:{self.port}/ws"

    # --- the clock -------------------------------------------------------------
    async def _run_clock(self) -> None:
        await self._first_ready.wait()
        await asyncio.sleep(self.start_after)
        self.world.start()
        await self._push_all()
        # Bots move mid-tick, so their offers and acceptances arrive between
        # ticks, as they would from other players.
        half = self.world.rules.tick_duration_ms / 2000
        while self.world.phase is model.Phase.PHASE_RUNNING:
            await asyncio.sleep(half)
            bots_act(self.world, self.rng)
            await self._push_all()
            await asyncio.sleep(half)
            self.world.advance()
            if self.on_tick is not None:
                self.on_tick(self.world)
            await self._push_all()
        self.finished.set()

    # --- connections -------------------------------------------------------------
    def _route(self, connection, request):
        if request.path.split("?", 1)[0] != "/ws":
            return connection.respond(404, "not found\n")
        if not _token(request.headers.get("Authorization", "")):
            return connection.respond(401, "a bearer token is required\n")
        offered = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
        if SUBPROTOCOL not in offered:
            return connection.respond(400, f"request the {SUBPROTOCOL} subprotocol\n")
        return None

    def station_for(self, token: str) -> str:
        named = token.removeprefix(TOKEN_PREFIX)
        return named if token.startswith(TOKEN_PREFIX) and named in self.world.stations else self.settings.station

    async def _handler(self, connection) -> None:
        station_id = self.station_for(_token(connection.request.headers.get("Authorization", "")))
        previous = self.sessions.get(station_id)
        session = _Session(connection, station_id)
        self.sessions[station_id] = session
        self.world.stations[station_id].claimed = True  # its bot stands down
        if previous is not None:
            await self._send(previous, protocol_error(
                model.ControlCode.CONTROL_CODE_SESSION_FENCED, close=True, run_id=self.world.run_id))
            await previous.connection.close()
        try:
            await self._send_state(session)
            async for raw in connection:
                await self._on_frame(session, raw)
        except ConnectionClosed:
            pass
        finally:
            if self.sessions.get(station_id) is session:
                del self.sessions[station_id]

    async def _on_frame(self, session: _Session, raw) -> None:
        if not isinstance(raw, bytes) or len(raw) > self.world.rules.max_command_bytes:
            await self._send(session, protocol_error(model.ControlCode.CONTROL_CODE_BAD_MESSAGE, close=False))
            return
        try:
            message = decode.decode_client_bytes(raw)
        except Exception:
            await self._send(session, protocol_error(model.ControlCode.CONTROL_CODE_BAD_MESSAGE, close=False))
            return

        arm, inner = message.which(), message.inner()
        if arm in ("ready", "sync"):
            problem = self._session_problem(inner)
            if problem is not None:
                await self._send(session, problem)
                await session.connection.close()
            elif arm == "ready":
                session.ready = inner.ready
                await self._send(session, model.Readiness(
                    type=model.ReadinessType.READINESS_TYPE_READINESS,
                    protocol_version=PROTOCOL_VERSION,
                    run_id=self.world.run_id,
                    ready=inner.ready,
                    snapshot_sequence=inner.snapshot_sequence,
                ))
                if inner.ready:
                    self._first_ready.set()
            else:
                await self._send_state(session)
            return

        if not session.ready:
            await self._send(session, protocol_error(
                model.ControlCode.CONTROL_CODE_BAD_MESSAGE, close=False,
                run_id=inner.run_id, request_id=inner.request_id))
            return
        answer = self.world.command(session.station_id, inner)
        await self._send(session, answer)
        if isinstance(answer, model.ProtocolError):
            if answer.close_session:
                await session.connection.close()
            return
        await self._push_all()

    def _session_problem(self, inner) -> model.ProtocolError | None:
        if inner.protocol_version != PROTOCOL_VERSION:
            return protocol_error(model.ControlCode.CONTROL_CODE_UNSUPPORTED_VERSION, close=True, run_id=inner.run_id)
        if inner.run_id != self.world.run_id:
            return protocol_error(model.ControlCode.CONTROL_CODE_RUN_MISMATCH, close=True, run_id=inner.run_id)
        return None

    # --- sending -----------------------------------------------------------------
    async def _push_all(self) -> None:
        """Everyone gets a fresh state: the world moved, and states are snapshots."""
        for session in list(self.sessions.values()):
            await self._send_state(session)

    async def _send_state(self, session: _Session) -> None:
        session.sequence += 1
        await self._send(session, self.world.state_for(session.station_id, session.sequence))

    async def _send(self, session: _Session, inner) -> None:
        arm = {
            model.State: "state",
            model.Result: "result",
            model.ProtocolError: "protocol_error",
            model.Readiness: "readiness",
        }[type(inner)]
        try:
            await session.connection.send(encode.encode_server_bytes(model.ServerMessage(**{arm: inner})))
        except ConnectionClosed:
            pass


def _token(header: str) -> str:
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""
