# [how bazaar](https://www.youtube.com/watch?v=C2cMG33mWVY)

![Spaceport Bazaar](artifacts/hero.png)

## Watch your own planet

The live dashboard is a separate program that watches this client from the
outside. It is not part of this repository yet. With it checked out as
`bazaar-dashboard/`, run these:

```bash
cp .env.example .env                                  # then paste your planet's token
cd bazaar-dashboard && ./setup.sh .. && ./start.sh    # once, then each session
python -m bazaar --policy jesus                       # in another terminal, from this repo
```

The dashboard's README covers how it hooks in and what it shows.

## Docs

- [Architecture](docs/architecture.md) — how the layers fit together and why
- [`model.py` contract](docs/model-contract.md) — what the domain model must define
