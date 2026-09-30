"""Serves the dashboard page and pushes each new view to it.

One port does both jobs.  ``websockets`` can answer a plain HTTP request from
``process_request`` before any handshake, so the page, its skins, a JSON
snapshot for ``curl``, and the live feed all come from a library the client
already uses.

Pushing uses ``broadcast``, which never waits on a browser: a slow or stalled
tab loses a frame rather than holding up the trading loop.
"""

from pathlib import Path

from websockets.asyncio.server import broadcast, serve
from websockets.exceptions import ConnectionClosed

from .hub import Dashboard

PAGE = Path(__file__).with_name("index.html")
SKINS = Path(__file__).with_name("skins")
SKIN_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


class DashboardServer:
    """Serves one :class:`Dashboard` at ``/`` (page), ``/skins/*``, ``/view.json`` and ``/ws``."""

    def __init__(self, dashboard: Dashboard):
        self.dashboard = dashboard
        self.host: str | None = None
        self._server = None
        self._page = PAGE.read_text(encoding="utf-8")
        # Read once, like the page, and served only by exact name: no request
        # path is ever turned into a file path.
        self._skins = {
            f"/skins/{file.name}": (file.read_text(encoding="utf-8"), SKIN_TYPES[file.suffix])
            for file in sorted(SKINS.iterdir()) if file.suffix in SKIN_TYPES
        }
        dashboard.subscribe(self._push)

    async def start(self, host: str, port: int) -> str:
        """Start listening and return the page's URL.

        If the port is taken -- most likely by a second client running another
        station -- fall back to whatever port the OS offers, rather than refuse
        to trade over a display.
        """
        self.host = host
        try:
            self._server = await serve(self._handler, host, port, process_request=self._route)
        except OSError:
            self._server = await serve(self._handler, host, 0, process_request=self._route)
        return self.url

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # --- HTTP and WebSocket ---------------------------------------------------
    def _route(self, connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None  # carry on with the WebSocket handshake
        if path in ("/", "/index.html"):
            return _typed(connection.respond(200, self._page), "text/html; charset=utf-8")
        if path == "/view.json":
            return _typed(connection.respond(200, self.dashboard.payload()), "application/json")
        if path in self._skins:
            body, content_type = self._skins[path]
            return _typed(connection.respond(200, body), content_type)
        return connection.respond(404, "not found\n")

    async def _handler(self, connection) -> None:
        # A page opened mid-run gets the whole picture at once, not at the
        # next state.
        try:
            await connection.send(self.dashboard.payload())
            await connection.wait_closed()
        except ConnectionClosed:
            pass

    def _push(self, payload: str) -> None:
        if self._server is not None:
            broadcast(self._server.connections, payload)


def _typed(response, content_type: str):
    del response.headers["Content-Type"]
    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "no-store"
    return response
