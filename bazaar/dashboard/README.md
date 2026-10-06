# Bazaar live dashboard

A browser page showing your planet while the client plays: reserves and how long
they last, open offers, recent trades, what the agent is after, and every
command's outcome. It is served by the client itself on every run, read-only,
and never sends anything to the game.

## Run it on your own planet

```bash
git clone <this repo> && cd spaceport_bazaar

pip install -r requirements.txt
python -m grpc_tools.protoc -I artifacts/bazaar-protobuf-starter-linux \
    --python_out=bazaar/generated --pyi_out=bazaar/generated \
    artifacts/bazaar-protobuf-starter-linux/bazaar.proto

cp .env.example .env          # then paste your planet's token after BAZAAR_TOKEN=
python -m bazaar --policy hustler
```

Open the URL printed as `dashboard: http://127.0.0.1:8765/`.

Inside the devcontainer the `pip install` and `protoc` steps already ran, so
only the last two lines apply.

Your **token picks your planet**. The page takes the station name from the game
itself, so there is no station to configure. (The `connected to ... as P08` line
the CLI prints echoes the `--station` default, not your planet; ignore it.)

Choose a policy with `--policy utility|scrooge|hustler`.

## Options

| Flag | Effect |
| --- | --- |
| `--dashboard-port N` | Serve on another port (default 8765). If it is taken, e.g. by a second client on another planet, a free port is used and printed. |
| `--no-dashboard` | Do not serve the page. |
| `?skin=<id>` on the URL, or the header menu | Switch look. Skins live in `skins/`. |

`http://127.0.0.1:8765/view.json` is the same data as JSON, for `curl`.

The page binds to `127.0.0.1` only, since it shows your stock and plans. On a
remote machine, tunnel it: `ssh -L 8765:127.0.0.1:8765 <host>`. VS Code
forwards the port by itself from a devcontainer.

Thresholds and history lengths are `DashboardSettings` in `bazaar/config.py`.

## Run history

However a run ends -- finished, aborted, or cut off by Ctrl+C or a crash -- the
client leaves `runs/<session>/history.json` beside the logs. It is built from
`messages.jsonl` alone, in ticks (the log has no timestamps), for the history tab
to chart. To summarise runs that predate this, or to pick up a change to the
format:

```bash
python -m bazaar.dashboard.history            # every run under runs/
python -m bazaar.dashboard.history runs/20260930-130821
```

The file is `{schema_version, session, context, log, runs: [...]}`; there is one
entry in `runs` per run ID in the log, and each has:

| Key | What it holds |
| --- | --- |
| `result` | `status` (`finished`, `aborted` or `interrupted`), `progress`, first and last tick, whether our station failed, the server's outcome |
| `health` | final, minimum and maximum, shortage counts |
| `totals` | produced, consumed, unmet, imported and exported, per resource |
| `trades` | counts, what we gave and got, and the same per counterparty |
| `offers` | how the offers we made ended, and the acceptance rate |
| `commands` | sent, retried, and how each kind ended (`ok`, `failed`, `errored`, `unanswered`), with failure codes |
| `series` | one shared `tick` axis with health, inventory, unmet upkeep, trades and commands per tick |

`trades` and `totals` are the run's whole ledger, so a client restarted mid-game
counts the earlier session's trades too; `commands` and `series` cover only what
that log saw.

`history/` imports nothing from the rest of the client, so it moves out with the
dashboard unchanged. It expects `messages.jsonl` as `runlog.py` writes it.

## What is in this folder

| File | Job |
| --- | --- |
| `view.py` | Pure: one state in, the page's JSON out. |
| `hub.py` | `Dashboard`: remembers history and the action lifecycle. Hooks swallow their own errors, so a display bug can never stop a trade. |
| `server.py` | `DashboardServer`: the page, skins, `/view.json` and the live feed on one port. |
| `index.html`, `skins/` | The page, and one `<id>.css` + `<id>.js` pair per look. |
| `history/` | Turns a run's message log into `history.json`; see above. |

## Attaching it to a different client

Three lines of wiring, as in `bazaar/__main__.py`:

```python
from bazaar.dashboard import Dashboard, DashboardServer

dashboard = Dashboard(agent="my-agent")
await DashboardServer(dashboard).start("127.0.0.1", 8765)

BazaarClient(url, token, on_state=dashboard.on_state)   # states: reserves, offers, trades
```

That alone fills the page. To also show what your agent decided and how each
command ended, call `dashboard.decided(state, actions)`, `.sent(action,
request_id)`, `.resolved(request_id, result)`, `.rejected(action, reason)` and
`.errored(request_id, message)` from your loop, as `runner.run_utility` does.

**The catch:** the dashboard reads this repo's types, not raw protobuf. It
imports `bazaar.validation.model` (the `State` it is fed), `bazaar.actions`
(for `describe()` and the action classes), `bazaar.brain.store` and
`bazaar.config.DashboardSettings`. A client that already uses those can take
this folder as-is; any other client should be built on this repo instead.
