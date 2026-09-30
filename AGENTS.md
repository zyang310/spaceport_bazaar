# AGENTS.md

Instructions for AI agents working in this repository. Read this before editing.

For *current state* — what is built, what is broken, what to do next — read
[`docs/handoff.md`](docs/handoff.md). This file covers *how to work here*.

---

## 1. What this is

A Python client for **Bazaar**, a resource-trading simulation. The client
connects over a WebSocket, speaks binary Protocol Buffers, and runs a trading
agent that decides what to buy, sell, and advertise.

- Schema (source of truth): `artifacts/bazaar-protobuf-starter-linux/bazaar.proto`
- Protocol guide (authoritative on behaviour): `artifacts/bazaar-protobuf-starter-linux/README.md`
- **Never edit** either of those, or the server binaries.

Where this repo's docs and the starter README disagree, **the README wins.**

---

## 2. Commands

```bash
# Tests — 287 of them, all must pass. Fast (~2s).
python -m pytest tests/ -q

# One file
python -m pytest tests/test_scrooge.py -q

# Run the client against the live game (token from .env)
python -m bazaar --policy hustler

# Run one agent while logging what another would have done, sending nothing
python -m bazaar --policy scrooge --shadow hustler

# Every run serves a live dashboard at http://127.0.0.1:8765/ (JSON at /view.json).
# Turn it off, or move it
python -m bazaar --policy hustler --no-dashboard
python -m bazaar --policy hustler --dashboard-port 8800

# The bundled practice server (start it first, in another terminal)
cd artifacts/bazaar-protobuf-starter-linux && ./spaceport-validate-linux-arm64 --codec protobuf
python -m bazaar --policy scripted --url ws://127.0.0.1:3001/ws

# A local game with bot traders on the other stations: live rules, 1s ticks.
# Start it, then point any client at it. Any token works; sandbox-P03 plays P03.
python -m bazaar.sandbox
BAZAAR_TOKEN=sandbox python -m bazaar --policy hustler --url ws://127.0.0.1:3002/ws

# Standalone diagnostics (raw protobuf, practice server only)
python -m bazaar.network.walkthrough happy|edge|errors
```

Run everything from the repo root. `pyproject.toml` sets `pythonpath = ["."]`.

**Killing the practice server:** `pkill -f spaceport-validate` will match its own
shell and kill it. Use `pkill -f '[s]paceport-vali''date'` in a command that does
not otherwise contain the literal string.

---

## 3. Layout

| Path | Job |
| --- | --- |
| `bazaar/generated/` | Generated from `bazaar.proto`. Never edit. Gitignored. |
| `bazaar/validation/model.py` | Our domain vocabulary. Frozen dataclasses, real enums. |
| `bazaar/validation/decode.py` | Wire → domain. **May import `bazaar_pb2`.** |
| `bazaar/validation/encode.py` | Domain → wire. **May import `bazaar_pb2`.** |
| `bazaar/validation/commands.py` | Builds a `model.ClientMessage`. Validates nothing. |
| `bazaar/validation/limits.py` | Legality: request-ID format, expiry bounds, size. |
| `bazaar/network/transport.py` | `BazaarClient`. One reader task owns `recv()`. |
| `bazaar/brain/store.py` | World view + pure query helpers over a state. |
| `bazaar/brain/policy/` | The agents. One file per policy. |
| `bazaar/actions.py` | What a policy decided, before it becomes a command. |
| `bazaar/runner.py` | The loop, shadow mode, fixture capture. |
| `bazaar/config.py` | Connection settings and every tunable weight. |
| `bazaar/runlog.py` | Per-run JSONL logs under `runs/` (gitignored). |
| `bazaar/dashboard/` | Live page per run. `view.py` is pure; `hub.py` records the action lifecycle; `server.py` serves page + WebSocket on one port. Must never break trading: hooks swallow their own errors. |
| `bazaar/dashboard/skins/` | One `<id>.css` + `<id>.js` pair per look, switched in the browser (`?skin=<id>` or the header menu). `index.html` keeps layout and rendering; a skin's CSS is scoped to `:root[data-skin="<id>"]` and its script registers symbols plus the few shapes that differ (see `SKIN_DEFAULTS`). Add a skin by linking both files in `index.html`. |
| `bazaar/sandbox/` | Local game server for testing: `world.py` (rules, pure), `bots.py` (seeded traders), `server.py` (socket). Speaks `model` types via `encode.encode_server_bytes` / `decode.decode_client_bytes`. |
| `bazaar/network/probe.py`, `walkthrough.py` | Standalone diagnostics, outside the layers. |
| `bazaar/memory/` | Empty stubs. Nothing persists across ticks yet. |

---

## 4. The one architectural rule

**Only `encode.py` and `decode.py` may import the generated bindings.**
Everything else speaks `model` types. This is mechanically checkable:

```bash
grep -rn "bazaar_pb2" bazaar/ --include="*.py" | grep -v "^bazaar/generated/"
```

Expected hits, and no others:
- `bazaar/validation/encode.py`
- `bazaar/validation/decode.py`
- `bazaar/network/probe.py`, `bazaar/network/walkthrough.py` — standalone
  diagnostics that are deliberately outside the layered client.

If `bazaar_pb2` appears in `brain/`, `runner.py`, or `memory/`, the boundary has
a hole. See `docs/architecture.md` for why it exists.

---

## 5. Adding a policy

A policy is a pure function from a state to a list of actions. Recipe:

1. Add a `<Name>Weights` frozen dataclass to `bazaar/config.py` with a
   `DEFAULT_<NAME>` instance. **Every tunable lives there, not in the policy.**
2. Create `bazaar/brain/policy/<name>.py` with a subclass of
   `BasePolicy` (`bazaar/brain/policy/base.py`) that sets:
   - `name = "<name>"` (the CLI flag value)
   - `default_weights = config.DEFAULT_<NAME>`
   - `candidates(self, state) -> list[Action]`: everything it would like to
     do, unranked and unlimited.
   **Do not override `decide`.** The base class owns it: gate, then
   `candidates`, then a stable sort by `-score`, then `_within_limits`.
   Override `_within_limits` only for a rule that must see the chosen actions
   together, as `utility`'s reserve does. Shared helpers (`_order`, `cover`,
   `value`, `_affordable`, `_expiry`, `BUNDLE_ORDER`) live there too; use them
   rather than writing your own.
3. Register it in `bazaar/brain/policy/__init__.py`'s `AGENTS` dict. The CLI
   picks it up automatically for both `--policy` and `--shadow`, and
   `tests/test_policy_contract.py` starts holding it to the contract below.
4. Add `tests/test_<name>.py` for what makes the agent *good*. Build states
   with `tests/factories.py`. The contract is already tested; do not copy it.

### Contract every policy must honour

`BasePolicy` enforces the first four; `tests/test_policy_contract.py` checks all
of them against every registered agent.

- **Pure.** Same state in, same actions out. No clocks, no randomness, no I/O.
  A captured fixture must replay identically.
- **Gated.** Return `[]` unless `state.phase is PHASE_RUNNING` and
  `state.observation.health > 0`.
- **Deterministic ordering.** Generate candidates in `rules.resource_order`,
  then station-ID order. The base class sorts by `-score`; Python's sort is
  stable, so generation order breaks ties — do not rely on anything else.
- **Within limits.** Never exceed `new_commands_per_station_per_tick`,
  `max_open_outgoing_offers`, or `store.remaining_result_capacity(state)`.
- **Legal.** Every action must pass `limits.validate_command`.
- **Never assigns request IDs.** `runner.py` owns those.

Existing policies, as reference points:

| Policy | Thesis |
| --- | --- |
| `utility` | Trade on projected need over a 5-tick horizon. The baseline. |
| `scrooge` | Defend reserves. Only shops when short. Never gives anything away. |
| `hustler` | Volume over margin. Always listed, accepts at a small loss. Pays in its specialty, to anyone, and never buys it. |
| `scripted` | Not an agent — replays the guide's 10-step exercise. Drives the connection directly because it must react to results and protocol errors, not just states. |

---

## 6. Protocol gotchas

These are proto2 and server behaviours that have already caused bugs.

- **Empty lists must still be present.** `selling {}` means "I sell nothing";
  omitting `selling` is `BAD_MESSAGE`. `encode.py` calls `SetInParent()` on
  every list container. Do not "simplify" that away.
- **Bundle zeros must be written.** An unassigned field is absent, not zero.
  All three of `water`/`food`/`components` are always assigned.
- **Nullables have exactly one arm.** `null: true` or `value`. `null: false` is
  rejected. `decode.unwrap()` raises rather than guessing.
- **`expires_tick` is a tick, not a duration**, and must be strictly greater
  than the current tick. The live game runs at 5s/tick, so an expiry computed
  from a stale state goes bad within seconds.
- **Request IDs are unique per *run*, not per process.** Reusing an ID with a
  different body gives `RESULT_CODE_REQUEST_ID_CONFLICT`. Reusing it with an
  identical body is a legitimate retry that returns the stored result.
- **`snapshot_sequence` counts states on one connection**; `world_version`
  counts world revisions. Sync advances the first but not the second.
- **A state is a snapshot.** Replace the previous view wholesale. Inventory
  already includes settled trades; never add them again.
- **The practice server accepts only the scripted sequence.** Any other command
  ends it with `scenario mismatch` and it closes the connection without
  answering. That is expected, not a client bug.

---

## 7. Secrets and safety

- **Never commit a token.** `BAZAAR_TOKEN` lives in `.env` (gitignored) or the
  environment. `resolve_token()` prefers the environment, then `.env`, then a
  practice-server credentials file. Only the token's *source* is ever printed.
- `validation-credentials.json` and `validation-report.json` stay gitignored.
- `tests/fixtures/*.bin` are raw server frames and contain **no** tokens —
  tokens only ever appear in connection headers. Re-verify if you regenerate.
- `webb_docs/` is the user's private notes. It is excluded via
  `.git/info/exclude` (local only, not `.gitignore`). **Never commit it**, and
  never move that rule into `.gitignore`.
- **Do not `git push`** unless explicitly asked. Commit locally.
- Stage files by name. Avoid `git add -A` / `git add .` in this repo.

---

## 8. Style

Match the surrounding code. Concretely:

- Module docstrings explain *why the module exists*, not what each function does.
- Comments explain reasoning and non-obvious protocol constraints. Skip comments
  that restate the code.
- Frozen dataclasses for anything decoded from the server — a snapshot is a
  reported fact and nothing should be able to write to it.
- Test names are sentences: `test_a_zero_price_gift_is_accepted`.
- Tests may import `bazaar_pb2`; the boundary rule applies to `bazaar/`, not
  `tests/`.
- Type hints on public functions. No enforced formatter or linter config.

---

## 9. Before you finish

```bash
python -m pytest tests/ -q                                    # 287 passing
grep -rn "bazaar_pb2" bazaar/ --include="*.py" | grep -v "^bazaar/generated/"
git status --short                                            # no .env, no webb_docs/
```
