# xy (Deux axes)

A two-axis game: each round has two axes and a set of candidates. Players
drag every candidate onto the chart, submit, then see where everyone put
them: the median position of each candidate, their own answers linked to it,
and the cloud of all answers for the candidate they tap.

Inspired by [XY, le Jeu des axes](https://xy.delemazure.fr/). This is an
independent reimplementation of the mechanics: no code, text or images come
from it.

The first round is about the CentraleSupélec merger: Centrale vs Supélec,
absorption vs fusion.

## Adding a round

Drop a YAML file in [`xy/rounds/`](xy/rounds/README.md). Its `date` hides it
until that day, and the newest published round opens by default. Older
rounds stay playable from the "Autres manches" list.

## How it works

- FastAPI serves a plain HTML/JS page (no build step) and a small JSON API.
- Answers live in Postgres, one row per round, player and candidate. A player
  is a random UUID kept in the browser's localStorage: no account, no name,
  no cookie. Submitting again replaces the player's previous answers.
- Nothing stops one person from submitting from several browsers. It's a game
  among friends.

| Endpoint | |
|---|---|
| `GET /api/rounds` | published rounds, newest first |
| `GET /api/rounds/{id}` | axes and candidates |
| `PUT /api/rounds/{id}/answers/{player}` | `{"positions": {"rer-b": {"x": 0.8, "y": -0.6}}}`, x and y in [-1, 1], y up |
| `GET /api/rounds/{id}/results` | player count, and per candidate: count, median, up to 300 sampled points |

## Configuration

| Variable | |
|---|---|
| `XY_DATABASE_URL` | full Postgres URL, or set `XY_DB_HOST`, `XY_DB_PORT`, `XY_DB_NAME`, `XY_DB_USER`, `XY_DB_PASSWORD` |
| `XY_ROUNDS_DIR` | rounds directory, defaults to the one shipped in the image |
| `PORT` | 8000 |

## Development

```bash
uv sync
make dev            # Postgres container + app on :8000 (needs docker)
make check-format
make typecheck
make test           # starts a throwaway Postgres container
```
