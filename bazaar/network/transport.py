"""The WebSocket: bytes on and off the socket, plus the handshake headers.

:class:`BazaarClient` speaks bytes to the server and ``model`` types to its
callers, so nothing above it handles a frame.  It never imports the generated
bindings itself; ``encode``/``decode`` do that translation.

One background reader task is the only code that calls ``recv()``.  Everything
else waits on a future or a condition that the reader resolves, which is what
keeps request/result matching honest when the server interleaves states with
results.
"""

import asyncio
import json
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.typing import Subprotocol

from ..runlog import RunLog
from ..validation import decode, limits, model

SUBPROTOCOL = "bazaar.protobuf.v2"
DEFAULT_URL = "ws://127.0.0.1:3001/ws"

#: Frames larger than this are refused before they are buffered.
MAX_FRAME_BYTES = 2**22


class ConnectionFailed(RuntimeError):
    """The connection is unusable; every pending request fails with this."""


class ProtocolErrorReceived(RuntimeError):
    """The server answered a command with ``protocol_error`` instead of a result.

    Step 9 of the guide provokes one deliberately, so callers are expected to
    catch this and decide whether it was the error they wanted.
    """

    def __init__(self, error: model.ProtocolError):
        self.error = error
        super().__init__(
            f"protocol_error {error.code.name} "
            f"(request_id={error.request_id}, close_session={error.close_session})"
        )


def load_token(credentials_path: Path | str, station_id: str) -> str:
    """Read one station's token out of ``validation-credentials.json``.

    The token is returned, never logged: it belongs in a header and nowhere
    else.
    """
    players = json.loads(Path(credentials_path).read_text())["players"]
    for player in players:
        if player["station_id"] == station_id:
            return player["token"]
    raise KeyError(f"no player with station_id {station_id!r} in {credentials_path}")


class BazaarClient:
    """One connection to the practice server.

    Used as an async context manager::

        async with BazaarClient(url, token) as client:
            state = await client.first_state()
            await client.declare_ready(state.snapshot_sequence)
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        run_log: RunLog | None = None,
        on_state=None,
        on_frame=None,
    ):
        self.url = url
        self._token = token
        self.run_log = run_log
        self.on_state = on_state
        #: Called with (raw_bytes, decoded_message) for every frame read,
        #: which is how fixture capture gets the bytes exactly as sent.
        self.on_frame = on_frame

        self.state: model.State | None = None
        #: Every state in arrival order.  The scripted exercise checks each
        #: snapshot individually, and steps 4-6 deliver three in a row.
        self.states: list[model.State] = []
        self.sent = 0
        self.received = 0
        #: protocol errors that named no request, so nobody was waiting for them
        self.unmatched_errors: list[model.ProtocolError] = []

        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._readiness: asyncio.Future | None = None
        self._state_changed = asyncio.Condition()
        self._closed: Exception | None = None

    # --- lifecycle --------------------------------------------------------
    async def __aenter__(self) -> "BazaarClient":
        self._ws = await connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self._token}"},
            subprotocols=[Subprotocol(SUBPROTOCOL)],
            max_size=MAX_FRAME_BYTES,
        )
        # The subprotocol selects the message format; if the server did not
        # confirm ours, anything we send would be read as the wrong format.
        if self._ws.subprotocol != SUBPROTOCOL:
            await self._ws.close()
            raise ConnectionFailed(
                f"server selected subprotocol {self._ws.subprotocol!r}, wanted {SUBPROTOCOL!r}"
            )
        self._reader = asyncio.create_task(self._read_loop(), name="bazaar-reader")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._fail_pending(ConnectionFailed("connection closed"))

    def _fail_pending(self, error: Exception) -> None:
        """Nothing may hang once the connection is gone."""
        self._closed = self._closed or error
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        if self._readiness is not None and not self._readiness.done():
            self._readiness.set_exception(error)

    # --- the reader -------------------------------------------------------
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    raise ConnectionFailed(f"expected a binary frame, got text: {raw!r}")
                self.received += 1
                message = decode.decode_bytes(raw)
                if self.on_frame is not None:
                    self.on_frame(raw, message)
                if self.run_log is not None:
                    self.run_log.message("received", message)
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(exc if isinstance(exc, ConnectionFailed) else ConnectionFailed(str(exc)))
            raise
        else:
            self._fail_pending(ConnectionFailed("server closed the connection"))

    async def _dispatch(self, message: model.ServerMessage) -> None:
        kind = message.which()
        if kind == "state":
            await self._on_state(message.state)
        elif kind == "result":
            self._resolve(message.result.request_id, message.result)
        elif kind == "readiness":
            if self._readiness is not None and not self._readiness.done():
                self._readiness.set_result(message.readiness)
        elif kind == "protocol_error":
            self._on_protocol_error(message.protocol_error)

    async def _on_state(self, state: model.State) -> None:
        # A snapshot replaces the previous view wholesale; it is never merged.
        self.state = state
        self.states.append(state)
        if self.on_state is not None:
            self.on_state(state)
        async with self._state_changed:
            self._state_changed.notify_all()

    def _on_protocol_error(self, error: model.ProtocolError) -> None:
        # An error naming a request belongs to whoever is waiting for it.
        if error.request_id is not None and error.request_id in self._pending:
            self._resolve_error(error.request_id, ProtocolErrorReceived(error))
        else:
            self.unmatched_errors.append(error)
        if error.close_session:
            self._fail_pending(ConnectionFailed(f"session closed by {error.code.name}"))

    def _resolve(self, request_id: str, value) -> None:
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(value)

    def _resolve_error(self, request_id: str, error: Exception) -> None:
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_exception(error)

    # --- sending ----------------------------------------------------------
    async def send(self, message: model.ClientMessage, raw: bytes | None = None) -> None:
        """Put one command on the socket, validating it first if needed."""
        if self._closed is not None:
            raise self._closed
        if raw is None:
            if self.state is None:
                raise ConnectionFailed("cannot validate a command before the first state")
            raw = limits.validate_command(message, self.state)
        await self._ws.send(raw)
        self.sent += 1
        if self.run_log is not None:
            self.run_log.message("sent", message)

    async def request(self, message: model.ClientMessage, request_id: str, timeout: float = 10):
        """Send a command and wait for the ``Result`` the server matches to it.

        Raises :class:`ProtocolErrorReceived` if the server answers with a
        protocol error naming this request instead.
        """
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def declare_ready(self, snapshot_sequence: int, ready: bool = True, timeout: float = 10):
        """Send readiness and wait for the acknowledgement.

        Required on every connection, including reconnects.
        """
        from ..validation.commands import CommandBuilder

        self._readiness = asyncio.get_running_loop().create_future()
        builder = CommandBuilder(self.state.run_id)
        await self.send(builder.ready(snapshot_sequence, ready=ready))
        return await asyncio.wait_for(self._readiness, timeout=timeout)

    async def sync(self, timeout: float = 10) -> model.State:
        """Ask for a fresh state and wait for the snapshot it produces.

        Sync does not use a stored-command slot and does not advance the world
        version, but it does advance ``snapshot_sequence``.
        """
        from ..validation.commands import CommandBuilder

        target = 0 if self.state is None else self.state.snapshot_sequence + 1
        await self.send(CommandBuilder(self.state.run_id).sync())
        return await self.wait_for_snapshot(target, timeout=timeout)

    # --- waiting ----------------------------------------------------------
    async def _wait_until(self, predicate, timeout: float):
        async def waiter():
            async with self._state_changed:
                while True:
                    if self._closed is not None:
                        raise self._closed
                    if self.state is not None and predicate(self.state):
                        return self.state
                    await self._state_changed.wait()

        return await asyncio.wait_for(waiter(), timeout=timeout)

    async def first_state(self, timeout: float = 10) -> model.State:
        """The state the server pushes as soon as the connection opens."""
        return await self._wait_until(lambda s: True, timeout)

    async def wait_for_version(self, world_version: int, timeout: float = 10) -> model.State:
        """Wait until our view is at least as new as ``world_version``."""
        return await self._wait_until(lambda s: s.world_version >= world_version, timeout)

    async def wait_for_snapshot(self, snapshot_sequence: int, timeout: float = 10) -> model.State:
        return await self._wait_until(lambda s: s.snapshot_sequence >= snapshot_sequence, timeout)

    async def next_state(self, after_sequence: int, timeout: float = 10) -> model.State:
        return await self.wait_for_snapshot(after_sequence + 1, timeout=timeout)

    async def state_with_sequence(self, snapshot_sequence: int, timeout: float = 10) -> model.State:
        """The snapshot with exactly this sequence number.

        ``self.state`` only ever holds the newest snapshot, so a caller that
        needs to inspect each one in a burst reads them from the history
        instead of racing the reader for them.
        """
        await self.wait_for_snapshot(snapshot_sequence, timeout=timeout)
        for state in self.states:
            if state.snapshot_sequence == snapshot_sequence:
                return state
        raise ConnectionFailed(f"no state with snapshot_sequence {snapshot_sequence} was received")

    async def quiet(self, seconds: float = 0.4) -> model.State | None:
        """Wait a moment and report any state that arrives unexpectedly.

        The exercise's message counts only hold if the server sends exactly
        what the guide lists, so a step can check that nothing extra followed.
        """
        before = len(self.states)
        try:
            await asyncio.wait_for(self._state_arrived(before), timeout=seconds)
        except asyncio.TimeoutError:
            return None
        return self.states[-1]

    async def _state_arrived(self, before: int) -> None:
        async with self._state_changed:
            while len(self.states) <= before:
                await self._state_changed.wait()
