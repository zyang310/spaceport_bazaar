"""Scenario runner for the Bazaar practice server.

Each scenario expects a freshly started server (a restart creates a new run):

    python3 -m bazaar.network.walkthrough happy|edge|errors
"""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from google.protobuf import text_format
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

from bazaar.generated import bazaar_pb2 as pb

STARTER_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "bazaar-protobuf-starter-linux"
URL = "ws://127.0.0.1:3001/ws"
SUBPROTOCOL = "bazaar.protobuf.v2"
VERSION = "2.0"
STATION = "P01"


def load_token() -> str:
    creds = json.loads((STARTER_DIR / "validation-credentials.json").read_text())
    return next(p["token"] for p in creds["players"] if p["station_id"] == STATION)


def inv(state) -> tuple[int, int, int]:
    b = state.self.inventory
    return (b.water, b.food, b.components)


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        check.failures += 1


check.failures = 0


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.run_id = ""
        self.sent = 0
        self.received = 0

    async def recv(self, timeout: float = 10) -> "pb.ServerMessage":
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        assert isinstance(raw, bytes), "text frame received"
        self.received += 1
        return pb.ServerMessage.FromString(raw)

    async def recv_kinds(self, *kinds: str) -> list:
        """Receive one message per expected kind, in order; return the inner messages."""
        out = []
        for want in kinds:
            msg = await self.recv()
            got = msg.WhichOneof("message")
            check(f"received {want}", got == want, f"got {got}: {text_format.MessageToString(msg, as_one_line=True)}")
            out.append(getattr(msg, got))
        return out

    async def quiet(self, seconds: float = 0.4) -> None:
        try:
            msg = await self.recv(timeout=seconds)
            check("no unexpected extra message", False, text_format.MessageToString(msg, as_one_line=True))
        except asyncio.TimeoutError:
            check("no unexpected extra message", True)

    async def send(self, msg: "pb.ClientMessage") -> None:
        self.sent += 1
        await self.ws.send(msg.SerializeToString())

    # --- message builders -------------------------------------------------
    def ready(self, seq: int, ready: bool = True) -> "pb.ClientMessage":
        m = pb.ClientMessage()
        m.ready.type = pb.READY_TYPE_READY
        m.ready.protocol_version = VERSION
        m.ready.run_id = self.run_id
        m.ready.ready = ready
        m.ready.snapshot_sequence = seq
        return m

    def sync(self) -> "pb.ClientMessage":
        m = pb.ClientMessage()
        m.sync.type = pb.SYNC_TYPE_SYNC
        m.sync.protocol_version = VERSION
        m.sync.run_id = self.run_id
        return m

    def _cmd(self, field: str, type_field: str, type_value: int, request_id: str):
        m = pb.ClientMessage()
        c = getattr(m, field)
        c.type = type_value
        c.protocol_version = VERSION
        c.run_id = self.run_id
        c.request_id = request_id
        return m, c

    def advertise(self, request_id: str, selling=(), seeking=(), expires: int = 6):
        m, c = self._cmd("advertise", "type", pb.ADVERTISE_TYPE_ADVERTISE, request_id)
        c.body.selling.items.extend(selling)
        c.body.seeking.items.extend(seeking)
        c.body.expires_tick = expires
        return m

    def offer(self, request_id: str, recipient: str, give, receive, expires: int = 6):
        m, c = self._cmd("offer", "type", pb.OFFER_COMMAND_TYPE_OFFER, request_id)
        c.body.recipient_id = recipient
        for bundle, vals in ((c.body.give, give), (c.body.receive, receive)):
            bundle.water, bundle.food, bundle.components = vals
        c.body.expires_tick = expires
        return m

    def accept(self, request_id: str, offer_id: str):
        m, c = self._cmd("accept", "type", pb.ACCEPT_TYPE_ACCEPT, request_id)
        c.body.offer_id = offer_id
        return m

    def withdraw(self, request_id: str, object_id: str):
        m, c = self._cmd("withdraw", "type", pb.WITHDRAW_TYPE_WITHDRAW, request_id)
        c.body.object_id = object_id
        return m

    # --- handshake --------------------------------------------------------
    async def read_first_state(self):
        (state,) = await self.recv_kinds("state")
        self.run_id = state.run_id
        return state

    async def confirm_ready(self, seq: int):
        await self.send(self.ready(seq))
        (r,) = await self.recv_kinds("readiness")
        check("readiness ready=true, run_id and snapshot_sequence echoed",
              r.ready and r.run_id == self.run_id and r.snapshot_sequence == seq)


@asynccontextmanager
async def open_session(token: str | None = None, subprotocol: str | None = SUBPROTOCOL):
    headers = {"Authorization": f"Bearer {token or load_token()}"}
    protos = [Subprotocol(subprotocol)] if subprotocol else None
    async with connect(URL, additional_headers=headers, subprotocols=protos, max_size=2**22) as ws:
        yield Session(ws)


def check_result(result, request_id: str, world_version: int | None = None) -> None:
    check(f"result {request_id}: ok / RESULT_CODE_OK",
          result.request_id == request_id and result.ok and result.code == pb.RESULT_CODE_OK,
          text_format.MessageToString(result, as_one_line=True))
    if world_version is not None:
        check(f"result {request_id}: processed_version={world_version}", result.processed_version == world_version)


def check_state(state, *, world: int, seq: int, inventory: tuple, transactions: int | None = None) -> None:
    check(f"state seq={seq} world={world}", (state.snapshot_sequence, state.world_version) == (seq, world),
          f"got seq={state.snapshot_sequence} world={state.world_version}")
    check(f"inventory {inventory}", inv(state) == inventory, f"got {inv(state)}")
    check("tick 0 / PHASE_RUNNING", state.tick == 0 and state.phase == pb.PHASE_RUNNING)
    if transactions is not None:
        check(f"{transactions} transactions", len(state.transactions.items) == transactions)


# --- scenarios -------------------------------------------------------------
async def happy() -> None:
    """README steps 1-10 exactly, with every documented check."""
    async with open_session() as s:
        print("step 1: state + readiness")
        st = await s.read_first_state()
        check_state(st, world=2, seq=1, inventory=(30, 30, 30))
        check("station P01, specialty WATER",
              st.self_station_id == "P01" and st.self.specialty == pb.RESOURCE_WATER)
        p02 = [a for a in st.advertisements.items if a.station_id == "P02"]
        check("P02 advertises food, seeks water",
              len(p02) == 1 and list(p02[0].selling.items) == [pb.RESOURCE_FOOD]
              and list(p02[0].seeking.items) == [pb.RESOURCE_WATER])
        check("rules: max_request_records_per_station == 5", st.rules.max_request_records_per_station == 5)
        await s.confirm_ready(1)
        await s.quiet()

        print("step 2: advertise water for food")
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-advertise-1")
        check_state(st, world=3, seq=2, inventory=(30, 30, 30))
        mine = [a for a in st.advertisements.items if a.station_id == "P01"]
        check("own advertisement listed", len(mine) == 1)
        await s.quiet()

        print("step 3: replace advertisement with request for components")
        await s.send(s.advertise("student-advertise-seeking-1", [], [pb.RESOURCE_COMPONENTS]))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-advertise-seeking-1")
        check_state(st, world=4, seq=3, inventory=(30, 30, 30))
        adv_id = r.object_id.value
        check("object_id present", r.object_id.WhichOneof("kind") == "value" and bool(adv_id))
        active = [a for a in st.advertisements.items
                  if a.station_id == "P01" and a.status == pb.PUBLICATION_STATUS_ACTIVE]
        check("one active P01 ad: sells nothing, seeks components",
              len(active) == 1 and not active[0].selling.items
              and list(active[0].seeking.items) == [pb.RESOURCE_COMPONENTS])
        await s.quiet()

        print("steps 4-6: offer 2 water for 1 food; P02 accepts; P02 gifts a component")
        await s.send(s.offer("student-offer-1", "P02", (2, 0, 0), (0, 1, 0)))
        r, st4, st5, st6 = await s.recv_kinds("result", "state", "state", "state")
        check_result(r, "student-offer-1")
        check_state(st4, world=5, seq=4, inventory=(30, 30, 30))
        own = [o for o in st4.offers.items if o.proposer_id == "P01"]
        check("own offer OPEN", len(own) == 1 and own[0].status == pb.OFFER_STATUS_OPEN)
        check_state(st5, world=6, seq=5, inventory=(28, 31, 30), transactions=1)
        check("offer ACCEPTED",
              [o.status for o in st5.offers.items if o.proposer_id == "P01"] == [pb.OFFER_STATUS_ACCEPTED])
        check_state(st6, world=7, seq=6, inventory=(28, 31, 30))
        gifts = [o for o in st6.offers.items if o.proposer_id == "P02" and o.status == pb.OFFER_STATUS_OPEN]
        check("open P02 gift: give (0,0,1), receive zeros",
              len(gifts) == 1
              and (gifts[0].give.water, gifts[0].give.food, gifts[0].give.components) == (0, 0, 1)
              and (gifts[0].receive.water, gifts[0].receive.food, gifts[0].receive.components) == (0, 0, 0))
        gift_id = gifts[0].offer_id
        await s.quiet()

        print("step 7: accept the gift")
        await s.send(s.accept("student-accept-1", gift_id))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-accept-1")
        check("result carries transaction_id", r.transaction_id.WhichOneof("kind") == "value")
        check_state(st, world=8, seq=7, inventory=(28, 31, 31), transactions=2)
        check("ad seeking components still active",
              any(a.advertisement_id == adv_id and a.status == pb.PUBLICATION_STATUS_ACTIVE
                  for a in st.advertisements.items))
        await s.quiet()

        print("step 8: withdraw advertisement")
        await s.send(s.withdraw("student-withdraw-1", adv_id))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-withdraw-1")
        check_state(st, world=9, seq=8, inventory=(28, 31, 31), transactions=2)
        check("ad no longer ACTIVE",
              not any(a.advertisement_id == adv_id and a.status == pb.PUBLICATION_STATUS_ACTIVE
                      for a in st.advertisements.items))
        await s.quiet()

        print("step 9: intentional request-capacity error")
        await s.send(s.advertise("student-advertise-2", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        (e,) = await s.recv_kinds("protocol_error")
        check("CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, close_session=false",
              e.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED and not e.close_session)
        check("error request_id matches", e.request_id.WhichOneof("kind") == "value"
              and e.request_id.value == "student-advertise-2")
        await s.quiet()

        print("step 10: sync for final state")
        await s.send(s.sync())
        (st,) = await s.recv_kinds("state")
        check_state(st, world=9, seq=9, inventory=(28, 31, 31), transactions=2)
        check("5 stored request results", len(st.request_results.items) == 5)
        t = st.self
        check("imported (0,1,1), exported (2,0,0)",
              (t.imported_total.water, t.imported_total.food, t.imported_total.components) == (0, 1, 1)
              and (t.exported_total.water, t.exported_total.food, t.exported_total.components) == (2, 0, 0))
        await s.quiet()
        check(f"sent 8 / received 16 messages (got {s.sent}/{s.received})", (s.sent, s.received) == (8, 16))


async def edge() -> None:
    """Documented side paths: not-ready gating, sync, retry, ID conflict, reconnect."""
    async with open_session() as s:
        print("sync + trading before readiness")
        st = await s.read_first_state()
        await s.send(s.sync())
        (st2,) = await s.recv_kinds("state")
        check("sync while not ready: seq 2, world unchanged",
              (st2.snapshot_sequence, st2.world_version) == (2, st.world_version))
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        (e,) = await s.recv_kinds("protocol_error")
        check("trade before ready -> BAD_MESSAGE, session kept",
              e.code == pb.CONTROL_CODE_BAD_MESSAGE and not e.close_session)
        await s.send(s.ready(2, ready=False))
        (r,) = await s.recv_kinds("readiness")
        check("ready:false acknowledged as ready=false", not r.ready)
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        (e,) = await s.recv_kinds("protocol_error")
        check("trade while ready=false -> BAD_MESSAGE", e.code == pb.CONTROL_CODE_BAD_MESSAGE)
        await s.confirm_ready(2)
        await s.quiet()

        print("step 2, exact retry, and request-ID conflict")
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-advertise-1", world_version=3)
        check_state(st, world=3, seq=3, inventory=(30, 30, 30))
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        r2, st = await s.recv_kinds("result", "state")
        check("retry returns the stored result unchanged", r2 == r)
        check_state(st, world=3, seq=4, inventory=(30, 30, 30))
        await s.quiet()
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_FOOD], [pb.RESOURCE_WATER]))
        msg = await s.recv()
        kind = msg.WhichOneof("message")
        print("   conflicting reuse ->", kind, text_format.MessageToString(getattr(msg, kind), as_one_line=True))
        check("changed command with reused ID -> REQUEST_ID_CONFLICT",
              kind == "result" and msg.result.code == pb.RESULT_CODE_REQUEST_ID_CONFLICT)
        # The README does not say so, but the server follows the conflict result with a state.
        (st,) = await s.recv_kinds("state")
        check_state(st, world=3, seq=5, inventory=(30, 30, 30))
        await s.quiet()

    print("reconnect: state restarts at seq 1, readiness required again")
    async with open_session() as s:
        st = await s.read_first_state()
        check("progress retained, seq 1", st.snapshot_sequence == 1 and st.world_version == 3)
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        (e,) = await s.recv_kinds("protocol_error")
        check("retry before re-confirming readiness -> BAD_MESSAGE", e.code == pb.CONTROL_CODE_BAD_MESSAGE)
        await s.confirm_ready(1)
        await s.send(s.advertise("student-advertise-1", [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD]))
        r, st = await s.recv_kinds("result", "state")
        check_result(r, "student-advertise-1", world_version=3)
        check("state seq 2 world 3 after retry", (st.snapshot_sequence, st.world_version) == (2, 3))


async def errors() -> None:
    """Connection-level failures; the run itself is never advanced."""
    print("bad credentials -> HTTP 401")
    try:
        async with open_session(token="not-a-real-token"):
            check("rejected", False, "connection accepted")
    except InvalidStatus as e:
        check("HTTP 401", e.response.status_code == 401, str(e.response.status_code))

    print("wrong subprotocol / none -> HTTP 400")
    for proto in ("bazaar.json.v2", None):
        try:
            async with open_session(subprotocol=proto):
                check(f"subprotocol {proto!r} rejected", False, "connection accepted")
        except InvalidStatus as e:
            check(f"subprotocol {proto!r}: HTTP 400", e.response.status_code == 400, str(e.response.status_code))
        except Exception as e:  # e.g. NegotiationError if the server accepts without echoing
            check(f"subprotocol {proto!r} rejected", False, repr(e))

    print("malformed payloads on a live session")
    async with open_session() as s:
        await s.read_first_state()
        await s.ws.send(b"\xff\xff not protobuf")
        (e,) = await s.recv_kinds("protocol_error")
        check("garbage bytes -> BAD_MESSAGE, session kept",
              e.code == pb.CONTROL_CODE_BAD_MESSAGE and not e.close_session)
        await s.ws.send(b"")
        (e,) = await s.recv_kinds("protocol_error")
        check("empty frame -> BAD_MESSAGE", e.code == pb.CONTROL_CODE_BAD_MESSAGE)
        await s.ws.send("text frame")
        try:
            msg = await s.recv(timeout=2)
            print("   text frame ->", text_format.MessageToString(msg, as_one_line=True))
            check("text frame -> BAD_MESSAGE", msg.WhichOneof("message") == "protocol_error"
                  and msg.protocol_error.code == pb.CONTROL_CODE_BAD_MESSAGE)
        except (asyncio.TimeoutError, ConnectionClosed) as e:
            print("   text frame -> ", repr(e))
            check("text frame answered", False, repr(e))

    print("wrong protocol version / run id close the session")
    async with open_session() as s:
        st = await s.read_first_state()
        m = s.sync()
        m.sync.protocol_version = "1.0"
        await s.send(m)
        msg = await s.recv()
        kind = msg.WhichOneof("message")
        check("bad version -> UNSUPPORTED_VERSION + close_session",
              kind == "protocol_error" and msg.protocol_error.code == pb.CONTROL_CODE_UNSUPPORTED_VERSION
              and msg.protocol_error.close_session,
              text_format.MessageToString(msg, as_one_line=True))
    async with open_session() as s:
        await s.read_first_state()
        m = s.sync()
        m.sync.run_id = "some-other-run"
        await s.send(m)
        msg = await s.recv()
        kind = msg.WhichOneof("message")
        check("bad run_id -> RUN_MISMATCH + close_session",
              kind == "protocol_error" and msg.protocol_error.code == pb.CONTROL_CODE_RUN_MISMATCH
              and msg.protocol_error.close_session,
              text_format.MessageToString(msg, as_one_line=True))


SCENARIOS = {"happy": happy, "edge": edge, "errors": errors}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "happy"
    print(f"== scenario: {name}")
    asyncio.run(SCENARIOS[name]())
    print(f"== {name}: {'OK' if not check.failures else f'{check.failures} FAILED'}")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
