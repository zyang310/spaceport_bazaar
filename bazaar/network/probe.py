"""Minimal Bazaar connectivity probe: connect, read the first state, declare ready."""
import asyncio
import json
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.typing import Subprotocol

from bazaar.generated import bazaar_pb2 as pb

STARTER_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "bazaar-protobuf-starter-linux"

URL = "ws://127.0.0.1:3001/ws"
SUBPROTOCOL = "bazaar.protobuf.v2"
PROTOCOL_VERSION = "2.0"
STATION = "P01"
CREDENTIALS = STARTER_DIR / "validation-credentials.json"


def load_token() -> str:
    creds = json.loads(CREDENTIALS.read_text())
    return next(p["token"] for p in creds["players"] if p["station_id"] == STATION)


async def recv_message(ws) -> "pb.ServerMessage":
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    if not isinstance(raw, bytes):
        raise RuntimeError("expected a binary frame, got a text frame")
    return pb.ServerMessage.FromString(raw)


async def main() -> None:
    async with connect(
        URL,
        additional_headers={"Authorization": f"Bearer {load_token()}"},
        subprotocols=[Subprotocol(SUBPROTOCOL)],
        max_size=2**22,
    ) as ws:
        if ws.subprotocol != SUBPROTOCOL:
            raise RuntimeError(f"server chose subprotocol {ws.subprotocol!r}")
        print("connected, subprotocol:", ws.subprotocol)

        # The server pushes a state immediately after the connection opens.
        msg = await recv_message(ws)
        kind = msg.WhichOneof("message")
        if kind != "state":
            raise RuntimeError(f"first message was {kind!r}, expected 'state'")
        state = msg.state
        print(
            f"state: run_id={state.run_id} snapshot_sequence={state.snapshot_sequence} "
            f"phase={pb.Phase.Name(state.phase)} station={state.self_station_id}"
        )

        # Declare readiness, echoing the snapshot we just read.
        out = pb.ClientMessage()
        out.ready.type = pb.READY_TYPE_READY
        out.ready.protocol_version = PROTOCOL_VERSION
        out.ready.run_id = state.run_id
        out.ready.ready = True
        out.ready.snapshot_sequence = state.snapshot_sequence
        await ws.send(out.SerializeToString())

        # Wait for the readiness reply (ignore anything else, fail on protocol errors).
        while True:
            reply = await recv_message(ws)
            kind = reply.WhichOneof("message")
            if kind == "readiness":
                r = reply.readiness
                print(f"readiness: ready={r.ready} snapshot_sequence={r.snapshot_sequence}")
                if not r.ready:
                    raise RuntimeError("server reported ready=false")
                break
            if kind == "protocol_error":
                e = reply.protocol_error
                raise RuntimeError(
                    f"protocol_error: {pb.ControlCode.Name(e.code)} "
                    f"(close_session={e.close_session})"
                )
            print("ignoring message:", kind)


asyncio.run(main())
