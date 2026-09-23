# Architecture

## The idea

The brain should never know that Protobuf exists.

Everything we send and receive is defined by [`bazaar.proto`](../artifacts/bazaar-protobuf-starter-linux/bazaar.proto),
which is shaped for the wire, not for us: lists are hidden inside wrapper
messages, optional values are two-armed unions, and enums decode as bare
integers. If those shapes reach `brain/`, our trading logic ends up written in
the server's vocabulary instead of our own.

So we keep a boundary. Raw bytes are translated into our own types at the edge,
and only those types travel inward:

> **raw data → processed data → used by the brain**

This is the pattern usually called an *anti-corruption layer*.

## The loop

The two directions mirror each other.

```
        INBOUND                              OUTBOUND
  bytes off the socket                  bytes onto the socket
          |  network/transport.py                ^  network/transport.py
   bazaar_pb2.ServerMessage              bazaar_pb2.ClientMessage
          |  validation/decode.py                ^  validation/encode.py
    model.ServerMessage                   model.ClientMessage
          |  brain/store.py                      ^  validation/commands.py
                       brain/policy/utility.py
                     "decide what to do next"
```

`encode.py` and `decode.py` are a mirrored pair: both are pure translation, and
both are the only files allowed to import the generated bindings.

`commands.py` and `store.py` are the other mirrored pair: both sit next to the
brain and speak only our vocabulary. This is why `commands.py` contains no
Protobuf — it is on the domain side of the wall.

## The layers

| Path | Job |
| --- | --- |
| `bazaar/generated/` | Generated from `bazaar.proto`. Never edited, excluded from coverage. |
| `bazaar/network/transport.py` | The WebSocket. Bytes in, bytes out, plus the handshake headers. |
| `bazaar/validation/model.py` | Our vocabulary: the message types the rest of the code uses. |
| `bazaar/validation/decode.py` | Wire → domain. Unwraps lists, flattens nullables, names enums. |
| `bazaar/validation/encode.py` | Domain → wire. The exact inverse. |
| `bazaar/validation/commands.py` | Turns an intention into a `model.ClientMessage`. |
| `bazaar/validation/limits.py` | Legality: request-ID format, expiry bounds, command limits. |
| `bazaar/brain/store.py` | The authoritative view of the world, replaced wholesale per snapshot. |
| `bazaar/brain/policy/utility.py` | The decision. Pure function of state and memory. |
| `bazaar/brain/policy/scripted.py` | The guide's ten-step exercise, for validating everything above against a live server. |
| `bazaar/actions.py` | What a policy decided, before it becomes a command. |
| `bazaar/runner.py` | The loop, plus shadow mode and fixture capture. |
| `bazaar/config.py` | Connection settings and the agent's tunable weights. |
| `bazaar/runlog.py` | Per-run JSONL logs under `runs/<run_id>/`. |
| `bazaar/__main__.py` | The CLI. |
| `bazaar/memory/` | What we have learned across ticks: who trades honestly, what we have promised. |

Two jobs inside "processed" are deliberately kept apart. **Translation**
(`decode.py`) is mechanical and depends on nothing. **Validation**
(`limits.py`) needs the run's rules and our current state. Mixing them makes
both harder to test.

## The rule that keeps this honest

An abstraction layer is only real if nothing leaks through it, and that is
mechanically checkable:

```bash
grep -rn "bazaar_pb2\|generated" bazaar/ --include="*.py" | grep -v "^bazaar/generated/"
```

The only hits should be `encode.py` and `decode.py`, plus `network/probe.py`
and `network/walkthrough.py`. Those two are standalone diagnostics rather than
part of the client: they talk to the generated bindings on purpose, to check
what the server actually does. If `bazaar_pb2` ever appears in `brain/` or
`memory/`, the wall has a hole and the decoupling is decorative.

This is also what makes the coverage story simple: generated bindings are
excluded because generated code touches exactly two files.

## Where new code goes

- Reading a field off a server message → `decode.py`
- Deciding whether a command is allowed → `limits.py`
- Deciding whether a command is a *good idea* → `brain/policy/utility.py`
- Remembering something between ticks → `memory/`

## Example: the command API

`CommandBuilder` holds the `run_id` copied from the first state and builds any
of the six messages a client can send. Each returns a `model.ClientMessage`
with the correct arm set, ready for `encode.py`.

```python
b = CommandBuilder(run_id)

b.advertise("student-advertise-1", [Resource.RESOURCE_WATER], [Resource.RESOURCE_FOOD], 6)
b.offer("student-offer-1", "P02", give, receive, 6)   # give/receive are model.Bundle
b.accept("student-accept-1", offer_id)
b.withdraw("student-withdraw-1", advertisement_id)
b.ready(snapshot_sequence=1)
b.sync()
```

`ready` and `sync` take no `request_id`, because the schema does not give them
one. That asymmetry is the protocol, not an oversight.

The builder validates nothing on purpose; legality is `limits.py`'s job.

## Status

Built and validated against the practice server: the whole inbound and
outbound path, both policies, the runner, and the CLI.

```sh
python -m bazaar --policy scripted --shadow utility --capture-fixtures
python -m bazaar --policy utility --max-seconds 20
```

The scripted run passes all of the guide's checks, with the message counts it
predicts. Real frames from that run are committed in `tests/fixtures/`, and the
agent's decisions on them are pinned as tests.

Not yet built: `memory/`. The agent currently decides from the state alone, so
nothing is carried across ticks yet.
