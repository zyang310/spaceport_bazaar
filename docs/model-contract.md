# `model.py` contract

`bazaar/validation/model.py` holds our own version of every message in
[`bazaar.proto`](../artifacts/bazaar-protobuf-starter-linux/bazaar.proto). It is
the vocabulary the whole client speaks — see [architecture.md](architecture.md)
for why the boundary exists.

`commands.py` was written against this contract before `model.py` existed. The
module now exists and the contract below is settled; both questions it once
left open are answered, and the answers are marked **Decided** in place.

## Conventions

1. **Names mirror the proto exactly** — `Advertise`, `OfferCommand`,
   `AdvertiseBody`, `Bundle`, `Resource`. Message names, field names, enum
   names, and enum member names all match.
2. **Keyword construction**, with argument names identical to the proto fields.
   `commands.py` calls everything by keyword.
3. **Enums are real Python enums**, not ints. `Resource.RESOURCE_WATER`, never
   `1`. This is most of the point of having a domain model. Keep the **same
   integer values as the proto** (`RESOURCE_WATER = 1`), so `decode.py` can
   convert with a plain `Resource(value)` lookup.
4. **Prefer frozen dataclasses**, especially for anything decoded from a
   server message. A `state` is a fact the server reported; nothing in our code
   should be able to write to it. The guide is explicit that snapshots are
   replaced wholesale and never edited in place.
5. **Nullable wrappers flatten to `X | None`.** The proto's
   `NullableString { oneof kind { bool null; string value } }` becomes a plain
   optional field; `encode.py` and `decode.py` pick the arm.

## Decided: `ListResource` flattens

**Option A below was chosen.** `model.py` holds resource lists as plain Python
lists, and `encode.py` re-adds the wrapper on the way out. `commands.py` had
assumed this, so it needed no change. The reasoning follows.

The proto does not give `AdvertiseBody.selling` a plain list. It wraps it:

```
message ListResource { repeated Resource items = 1; }

message AdvertiseBody {
  required ListResource selling = 1;
  required ListResource seeking = 2;
  required uint64 expires_tick = 3;
}
```

The wrapper exists because proto2 cannot mark a `repeated` field `required`,
and cannot tell an empty list apart from a missing one. Wrapping the list in a
message restores that distinction, which matters because "I sell nothing" is a
real claim we need to make — guide step 3 sends exactly that. So on the wire:

| Sent | Meaning |
| --- | --- |
| `selling` absent | Invalid — `CONTROL_CODE_BAD_MESSAGE` |
| `selling {}` | "I sell nothing" |
| `selling { items: RESOURCE_WATER }` | "I sell water" |

That problem is specific to the wire. In Python, `[]` and `None` are already
different values, so the wrapper buys nothing and costs a `.items` on every
access.

**Option A — flatten** (what `commands.py` currently assumes):

```python
AdvertiseBody(selling=[Resource.RESOURCE_WATER], ...)
body.selling[0]
```

`encode.py` re-adds the wrapper on the way out; `decode.py` strips it coming in.

**Option B — keep the wrapper**, mirroring the proto exactly:

```python
AdvertiseBody(selling=ListResource(items=[Resource.RESOURCE_WATER]), ...)
body.selling.items[0]
```

`model.py` then matches the schema field for field, and `encode.py`/`decode.py`
have nothing to translate here.

If we pick B, the only change in `commands.py` is one line in `_resource_list`.

## Needed now — what `commands.py` imports

**Enums**

| Name | Members |
| --- | --- |
| `Resource` | `RESOURCE_WATER`, `RESOURCE_FOOD`, `RESOURCE_COMPONENTS` |
| `AdvertiseType` | `ADVERTISE_TYPE_ADVERTISE` |
| `OfferCommandType` | `OFFER_COMMAND_TYPE_OFFER` |
| `AcceptType` | `ACCEPT_TYPE_ACCEPT` |
| `WithdrawType` | `WITHDRAW_TYPE_WITHDRAW` |
| `ReadyType` | `READY_TYPE_READY` |
| `SyncType` | `SYNC_TYPE_SYNC` |

The single-member enums look silly but are required fields in the schema, so
every command carries one.

**Bodies**

| Name | Fields |
| --- | --- |
| `Bundle` | `water`, `food`, `components` (all ints, zeros included) |
| `AdvertiseBody` | `selling`, `seeking`, `expires_tick` |
| `OfferBody` | `recipient_id`, `give`, `receive`, `expires_tick` |
| `AcceptBody` | `offer_id` |
| `WithdrawBody` | `object_id` |
| `ListResource` | `items` — only if we pick Option B above |

**Commands**

| Name | Fields |
| --- | --- |
| `Advertise` | `type`, `protocol_version`, `run_id`, `request_id`, `body` |
| `OfferCommand` | `type`, `protocol_version`, `run_id`, `request_id`, `body` |
| `Accept` | `type`, `protocol_version`, `run_id`, `request_id`, `body` |
| `Withdraw` | `type`, `protocol_version`, `run_id`, `request_id`, `body` |
| `Ready` | `type`, `protocol_version`, `run_id`, `ready`, `snapshot_sequence` |
| `Sync` | `type`, `protocol_version`, `run_id` |

`Ready` and `Sync` genuinely have no `request_id` and no `body`.

**Envelope**

`ClientMessage`, with one optional attribute per arm — `advertise`, `offer`,
`accept`, `withdraw`, `sync`, `ready` — exactly one of which is set.

## Needed later — the receiving side

Not required for `commands.py`, but `decode.py` will need all of it:

- **Envelope:** `ServerMessage` with arms `state`, `result`, `protocol_error`,
  `readiness`
- **Messages:** `State`, `Result`, `ProtocolError`, `Readiness`, `Offer`,
  `Transaction`, `Advertisement`, `DirectoryEntry`, `StationObservation`,
  `PublicRules`, `PlayerOutcome`
- **Enums:** `Phase`, `OfferStatus`, `PublicationStatus`, `ResultCode`,
  `ControlCode`
- **Nullables:** `NullableString`, `NullableUint`, `NullableBool`,
  `NullablePlayerOutcome` — all flattening to `X | None` per convention 5

One naming snag: `State.self` in the proto collides with Python's `self`.
**Decided: `observation`.** `decode.py`, `brain/store.py`, and the policies all
use that name, so a state reads `state.observation.inventory`.

## Verifying it

```bash
python -c "from bazaar.validation.commands import CommandBuilder; \
           print(CommandBuilder('run-1').sync())"
```

If that prints a `ClientMessage` with the `sync` arm set, the contract holds.
It does, and `tests/test_commands.py` now covers all six arms; `tests/factories.py`
builds whole `State` objects for the tests that need one.
