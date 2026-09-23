# Handoff: Bazaar client

State of the repository as of **2026-09-23**, commit `fc4bb07`.

How to *work* in this repo is in [`../AGENTS.md`](../AGENTS.md). This document
is the current state: what exists, what is proven, what is broken, what is next.

---

## STATUS

```yaml
commit: fc4bb07
branch: main
branch_state: ahead of origin/main by 2, behind 0    # reconciled, not pushed
working_tree: 1 uncommitted change (bazaar/config.py, STATION_ID P01 -> P08)
tests: 165 passing, 0 failing, runtime ~0.6s
python: 3.14.7
deps: protobuf, grpcio-tools, websockets>=14, pytest
platform: linux/aarch64 devcontainer
architecture_boundary: intact
practice_server: validated end to end, exit 0
live_game: connected, trading, two open bugs (see BUGS)
blocking_issues: none for development; BUG-1 and BUG-2 block useful live play
```

---

## COMPONENTS

| Component | Path | Lines | State |
| --- | --- | --- | --- |
| Domain model | `bazaar/validation/model.py` | 461 | Complete |
| Decode (wire→domain) | `bazaar/validation/decode.py` | 223 | Complete |
| Encode (domain→wire) | `bazaar/validation/encode.py` | 113 | Complete |
| Command builders | `bazaar/validation/commands.py` | 143 | Complete, pre-existing |
| Validator | `bazaar/validation/limits.py` | 88 | Complete |
| WebSocket client | `bazaar/network/transport.py` | 339 | Complete |
| World view | `bazaar/brain/store.py` | 98 | Complete |
| Policy: utility | `bazaar/brain/policy/utility.py` | 271 | Complete |
| Policy: scrooge | `bazaar/brain/policy/scrooge.py` | 414 | Complete |
| Policy: hustler | `bazaar/brain/policy/hustler.py` | 296 | Complete |
| Policy: scripted | `bazaar/brain/policy/scripted.py` | 342 | Complete, validated live |
| Actions | `bazaar/actions.py` | 98 | Complete |
| Runner | `bazaar/runner.py` | 246 | Complete, has BUG-1/BUG-2 |
| CLI | `bazaar/__main__.py` | 150 | Complete |
| Config | `bazaar/config.py` | 203 | Complete |
| Run logs | `bazaar/runlog.py` | 55 | Complete |
| Diagnostics | `bazaar/network/{probe,walkthrough}.py` | 486 | Complete, standalone |
| Memory | `bazaar/memory/*.py` | 0 | **EMPTY STUBS — not built** |

### Tests

```yaml
tests/test_model.py: 7             # domain model guarantees
tests/test_commands.py: 7          # builder assembly
tests/test_encode.py: 12           # proto2 presence, nullables, round trips
tests/test_limits.py: 18           # request IDs, expiry bounds, size
tests/test_token.py: 8             # token resolution order
tests/test_runner.py: 3            # deadline logic (unlimited by default)
tests/test_utility.py: 25          # utility policy, synthetic states
tests/test_scrooge.py: 34          # scrooge policy, synthetic states
tests/test_hustler.py: 26          # hustler policy, synthetic states
tests/test_utility_fixtures.py: 25 # utility policy, real captured states
total: 165
```

`tests/factories.py` builds whole `State` objects; pass only the fields a test
cares about. `tests/fixtures/*.bin` are 16 raw frames captured from a real
practice run — verified token-free.

---

## POLICIES

All three are pure functions of state, gated on `PHASE_RUNNING` and
`health > 0`, deterministic, and limit-aware. Tunables in `bazaar/config.py`.

| | `utility` | `scrooge` | `hustler` |
| --- | --- | --- | --- |
| Thesis | Projected need | Defend reserves | Volume over margin |
| Horizon | 5 | 10, capped to run end | 2 |
| Advertises | When position changes | Only when short | Always |
| Offers | Peers matching need | Only when short | Everyone, 2× per peer |
| Accepts | Positive value | Gifts always; else specialty-priced at ≥2× | Anything above `-1.0` |
| Gives gifts | No | **Never** (enforced by `_without_charity`) | No |
| Hard floor | Creates no new deficit | `danger_ticks = 5.0` cover | `floor_ticks = 2.0` cover |

On one identical comfortable state: `scrooge` → 0 actions, `utility` → 1,
`hustler` → 3.

---

## VALIDATION EVIDENCE

### Practice server — PASSED

`python -m bazaar --policy scripted --shadow utility --capture-fixtures`

```yaml
exit_code: 0
checks: 56 passed, 0 failed
messages: sent 8, received 16        # exactly as the guide predicts
final_inventory: [28, 31, 31]
transactions: 2
stored_results: 5
imported_total: [0, 1, 1]
exported_total: [2, 0, 0]
report_status: "sample exchange completed"
report_last_completed_step: 10
report_mismatch: null
shadow: 9 states seen, 9 actions proposed, 0 sent
```

### Live game — CONNECTED, TRADING, DEGRADED

Observed at `runs/20260923-141551/`:

```yaml
run_id: run-40
station: P08
specialty: RESOURCE_WATER
phase: PHASE_RUNNING
tick: 120
duration_ticks: 120                  # run at or past its end
tick_duration_ms: 5000               # 5s per tick — fast
world_version: 1413
inventory: {water: 262, food: 0, components: 73}
upkeep_per_tick: {water: 1, food: 1, components: 1}
health: 25                           # of max_health 100 — station is dying
open_offers_visible: 369
stored_results: 593
directory: [P01..P09]
```

**Live rules differ sharply from the practice server.** Do not tune against
practice numbers:

| Rule | Practice | Live |
| --- | --- | --- |
| `duration_ticks` | 12 | 120 |
| `tick_duration_ms` | 10000 | 5000 |
| `max_request_records_per_station` | 5 | **2048** |
| `new_commands_per_station_per_tick` | 10 | 10 |
| `max_open_outgoing_offers` | 24 | 24 |
| `max_*_ttl_ticks` | 12 | 12 |

The stored-result cap is 2048 live, not 5. Earlier warnings that the agents
would exhaust command capacity **do not apply to the live game.**

---

## BUGS

### BUG-1 — request IDs collide across process restarts (HIGH)

- **Symptom:** every command returns `RESULT_CODE_REQUEST_ID_CONFLICT`; the
  agent sends and nothing lands.
- **Cause:** `bazaar/runner.py:150` sets `issued = 0` and line 190 builds
  `f"utility-{issued:04d}"`. The counter resets each process start, but the
  server remembers request IDs for the whole *run*. Restarting the client
  reuses `utility-0001` with a different body, which the server treats as a
  conflicting reuse.
- **Evidence:** `runs/20260923-141551/` — every result `ok=False`.
- **Fix direction:** make IDs unique per run. Seed from the run ID plus a
  timestamp or a persisted counter, e.g. `f"{run_id[-6:]}-{epoch}-{n:04d}"`,
  kept inside the 64-char `^[A-Za-z0-9_-]{1,64}$` limit. Note the prefix is
  still `utility-` for every policy, which is also wrong.

### BUG-2 — commands validated twice, against a moving tick (HIGH)

- **Symptom:** `CommandRejected: expires_tick 61 must be greater than the
  current tick 61`, raised locally; the command never goes out.
- **Cause:** `runner.py` calls `limits.validate_command(message, state)` with
  the decision-time state and **discards the returned bytes**. It then calls
  `client.request(message, request_id)`, which calls `send(message)` with
  `raw=None`, so `transport.py` re-validates against `self.state` — by then
  several ticks newer. At 5s/tick with Hustler's 3-tick TTL the expiry is stale
  within seconds.
- **Evidence:** `runs/20260923-141540/summary.json`.
- **Fix direction:** either thread `raw` through `request()` so the validated
  bytes are the bytes sent, or compute `expires_tick` at send time from the
  freshest state. Threading `raw` alone is not enough — a genuinely stale
  expiry would then be rejected by the *server* instead. Prefer recomputing.

### BUG-3 — agents starve while hoarding their specialty (STRATEGIC)

- **Symptom:** live inventory `water: 262, food: 0`, health 25/100.
- **Cause:** upkeep is `(1,1,1)` — all three resources are consumed every tick.
  The agents accumulate the specialty they produce and never secure enough
  food. `scrooge` only shops below `danger_ticks`; with 262 water its average
  position looks healthy. No policy weights *survival* over *value*.
- **Fix direction:** treat any resource at zero cover as an emergency that
  overrides pricing entirely, and consider capping specialty accumulation.
  This is a policy design question, not a code defect.

---

## DECISIONS MADE

Recorded so they are not relitigated.

- **Plan vs. repo architecture.** The original plan specified a `bazaar_client/`
  package built directly on protobuf. The repo already had a documented
  anti-corruption layer. The user chose to adapt the plan into `bazaar/` and
  keep the boundary. Hence no `pb.py`/`wire.py`; those roles live in
  `encode`/`decode`/`limits`.
- **`ListResource` flattens** to plain Python lists (Option A in
  `model-contract.md`). `commands.py` already assumed it.
- **`State.self` is named `observation`** — `self` is unusable in methods.
- **Logging uses model dataclasses, not `MessageToDict`** — calling that in
  `transport.py` would put protobuf back outside the boundary.
- **`walkthrough.py` kept as a standalone diagnostic**, with its per-step checks
  ported into `ScriptedPolicy`.
- **`--max-seconds` defaults to unlimited.** It was 30, which looked like a
  disconnect while the live game sat in `PHASE_READY`.
- **Package is `bazaar`, invoked as `python -m bazaar`** (plan said
  `bazaar_client`).

---

## NEXT STEPS

Ordered by value.

1. **Fix BUG-1.** Nothing lands live until request IDs are unique per run.
2. **Fix BUG-2.** Recompute expiry at send time.
3. **Address BUG-3.** Add a survival override: zero cover on any resource
   outranks every other consideration.
4. **Commit or revert** the uncommitted `STATION_ID = "P08"` change.
5. **Push** — `main` is 2 ahead of `origin/main`, fast-forward, not yet pushed.
6. **Capture live fixtures.** All current fixtures are from the practice server,
   whose rules differ sharply. `--capture-fixtures` overwrites
   `tests/fixtures/` — write to a new directory instead.
7. **Build `bazaar/memory/`** if cross-tick learning is wanted. Empty stubs,
   referenced by `docs/architecture.md`, nothing depends on them.
8. **Delete `backup/pre-reconcile-140708`** once the push lands.

---

## SUMMARY (human)

The client is **finished and works**. It speaks the protocol correctly, passes
all 56 of the practice server's checks end to end with the exact message counts
the guide predicts, and has 165 fast tests including 25 that replay real
captured server frames.

There are **three trading agents**: `utility` (balanced), `scrooge` (hoards,
never gives anything away, only shops when running low) and `hustler` (trades
constantly, accepts small losses for volume). Each is a pure function of game
state, so they are fully testable offline, and each has its weights in one
config file.

The interesting part is what happened live. The client connected to the real
game as station P08 and traded — but two bugs stop it landing commands. Request
IDs restart at `0001` every time the process starts, while the server remembers
them for the whole run, so every command comes back as a conflict. And commands
get validated twice, the second time against a newer tick, so expiries go stale
before they are sent. Both are small, well-understood fixes in `runner.py` and
`transport.py`.

The more interesting problem is strategic: the station ended the run with **262
water, zero food, and 25% health.** All three resources are consumed every
tick, and every agent happily piles up the resource it produces while quietly
starving. No policy currently treats "about to die" as different from "trading
badly." That is a design gap, not a defect.

Also worth knowing: the live game is nothing like the practice server — 120
ticks at 5s each rather than 12, and 2048 stored command slots rather than 5.
Any tuning done against practice numbers should be re-checked.
