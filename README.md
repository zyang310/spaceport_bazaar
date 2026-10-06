# [how bazaar](https://www.youtube.com/watch?v=C2cMG33mWVY)

![Spaceport Bazaar](artifacts/hero.png)

## Watch your own planet

The live dashboard is a separate repository, `bazaar-dashboard`, that watches
this client from the outside. Keep it beside this repo (`../bazaar-dashboard`).
The devcontainer mounts it at `/workspaces/bazaar-dashboard` and runs its setup
on every rebuild. Then:

```bash
cp .env.example .env                                  # then paste your planet's token
/workspaces/bazaar-dashboard/start.sh                 # serve the page and wait
python -m bazaar --policy jesus                       # in another terminal, from this repo
```

Outside the devcontainer, run `../bazaar-dashboard/setup.sh .` once first.

The dashboard's README covers how it hooks in and what it shows.

## Docs

- [Architecture](docs/architecture.md) — how the layers fit together and why
- [`model.py` contract](docs/model-contract.md) — what the domain model must define
