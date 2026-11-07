# AGENTS.md — karaoke

Guide for coding agents. A KaraFun-like karaoke night for the owner's homelab, used
by French-speaking friends, live at https://karaoke.dixneuf19.fr.

## Product rules (keep them)

- **All UI text is in French.** Code, comments and logs are in English.
- **The big screen is the player.** A computer on the TV opens `/display/{code}`
  (QR code + queue, then the videos). Phones scan it and open `/r/{code}` to search,
  queue, reorder (drag and drop), pause/skip/restart/volume and send photos.
  Anyone in the room may control anything: it's a party.
- **Curated search first.** The default search runs only on the KaraFun YouTube
  channels (`KARAOKE_CHANNELS`, default `karafunfr,karafun`), in parallel, then
  re-ranked (`search.rank`: word overlap + fuzzy ratio). Searching all of YouTube
  needs an explicit tap and appends " karaoke" to the query. A pasted YouTube link
  resolves that one video. `strong: false` (no result has every query word) makes
  the phone offer YouTube up front.
- **Cancelled artists** (`cancelled.py`, `CANCELLED_ARTISTS`) stay playable but are
  flagged; the phone asks for a "J'assume" confirmation. The flag is computed
  server-side, never trusted from the phone. User-suggested cancellations: future.
- **Photos are anonymous on purpose** (guessing who sent one is the fun). Never
  expose the uploader in state, on the screen, or anywhere a client can read it.
  Phones remember their own photo ids in localStorage. Cancelling a photo only
  checks the uploader *name*: deliberately loose, friends may mess with each other.
- **Photo fairness, not FIFO** (`photos.PhotoQueue`): next photo comes from the
  uploader shown least recently; never-shown uploaders first; ties by upload order.
  One photo at a time, 10 s, tilted instant-photo print with washi tape; rotating
  corners during a song, centred between songs.
- **Resume after a screen reload**: the screen reports its position every 5 s; a
  reloaded screen resumes mid-song with a 3 s rewind and skips the "Au tour de…"
  interstitial (6 s, only for fresh songs).
- Out of scope: lights; scraping or pirating KaraFun's paid catalogue.

## Code map (`karaoke/karaoke/`)

| File | Role |
|---|---|
| `rooms.py`, `photos.py` | Pure state and logic, no I/O (Room, QueueItem, PhotoQueue). Unit-tested directly. |
| `db.py` | `Store`: Postgres via asyncpg. Schema, `room_tx`, videos/photos tables, NOTIFY. |
| `app.py` | FastAPI app + WebSockets. `Hub` = this replica's sockets + background loops. |
| `search.py` | yt-dlp flat search (channels / all YouTube / single link), title parsing, ranking. |
| `library.py` | yt-dlp download to `<data>/songs/<video_id>.mp4` (≤720p H.264 + AAC). |
| `cancelled.py` | Cancelled artists, matched on whole normalized words. |
| `config.py` | `Settings` from `KARAOKE_*` env vars. |
| `static/` | Plain HTML/CSS/JS, no build step: `index.html`, `display.html`, `guest.html`, `common.js` (socket + `esc`), `app.css`. SortableJS from jsdelivr. |

## How multi-replica state works

Prod runs 2 replicas. Everything a replica knows lives in Postgres or on the shared
`/data` volume; a process only keeps its open sockets.

1. A change goes through `Store.room_tx(code)`: `SELECT … FOR UPDATE` on the room
   row, load the whole aggregate (room, queue, photos), mutate it with the pure
   methods, save it in the same transaction (queue rows are deleted and rewritten).
2. On commit, `pg_notify('karaoke', {"room": code})` reaches every replica's
   LISTEN connection; each re-reads the snapshot (one REPEATABLE READ read) and
   pushes `{"type": "state"}` to its local sockets. One push loop per room
   (`Hub.push_state`): pushes stay in order and bursts coalesce. Sends go to all
   sockets in parallel with a 5 s timeout; a socket that doesn't keep up is closed
   (the page reconnects and gets the full state), so one frozen phone never holds
   up the TV. Display commands (`play`/`pause`/`restart`/`volume`) are sent
   as `{"room", "command"}` via `tx.command()` and forwarded only to screens.
3. Background loops run on **every** replica, so each must be coordinated:
   photo rotation every 0.5 s (`room_tx(skip_locked=True)`: one replica per room),
   download resume every 15 s (claim row in `videos`; also recreates video rows
   missing for queued songs, re-downloads lost files, and starts a ready song in
   idle rooms), prune every 10 min (rooms idle 24 h, orphan photo folders, temp
   files older than 1 h), LISTEN health check every 5 s (`SELECT 1`, reconnect).
4. Downloads: `videos` row is the claim (`pending → downloading → ready|error`);
   the owner heartbeats (≤15 s); another replica takes over after 60 s without one.
   Max 2 downloads per replica. A download writes into `songs/.tmp/<worker>-<id>/`
   and is renamed into `songs/` when complete, so the cache only ever holds whole
   files even if two replicas download the same video. `finish_download` only
   counts for the replica holding the claim. On shutdown a replica releases its
   claims. LRU eviction (`KARAOKE_CACHE_MAX_GB`, 20) under an advisory lock,
   never evicting a video still in a queue (re-checked in the `DELETE`). When a
   download finishes, idle rooms waiting on it advance.
5. Position reports (`position`) are a plain `UPDATE` without lock or NOTIFY:
   they are frequent and nobody needs a push for them. They only apply while
   `rooms.current_item_id` is the reported song.
6. The screen follows `state.state` for play/pause, not only the commands: a
   command lost during a reconnect is fixed by the next state push.

Invariants to keep:

- Never keep state only in process memory (the `downloading` set and photo-file
  deletion timers are local optimizations, not state).
- Mutate rooms only inside `room_tx`, with logic in `rooms.py`/`photos.py`.
- Keep NOTIFY payloads tiny (room code + optional command; Postgres caps them at
  8000 bytes). Replicas re-read state from the DB, never from the payload.
- Screen-only messages (`ended`, `player`, `position`) are gated by `is_display`;
  commands go only to screens.
- Schema changes go in `db.SCHEMA` and must be additive and idempotent
  (`IF NOT EXISTS`): it runs at every startup under an advisory lock, and a rolling
  update runs old and new code against the same schema.
- Frontend: any server string put into `innerHTML` goes through `esc()`, or use
  `textContent`.
- Public site, no login: validate what phones send. Video ids must match
  `[A-Za-z0-9_-]{11}` (`library.valid_video_id`: they become file paths and
  URLs); thumbnails are built server-side; queue capped at 200; WebSocket messages
  at 64 KiB (`__main__.py`); photos streamed to `/data/tmp` with a running size
  check (10 MB), then moved in; 30 pending photos per name, 300 per room.
- yt-dlp traffic comes from the home IP, so keep it modest: searches need a live
  `room`, run 4 at a time per replica and are cached 10 min in memory per replica.

## Protocol

- REST: `/healthz`, `/readyz` (DB ping), `POST /api/rooms`, `GET /api/rooms/{code}`,
  `/api/rooms/{code}/qr.svg`, `/api/search?room=&q=&everywhere=`, `POST
  /api/rooms/{code}/photos?uploader=` (raw body, JPEG/PNG/WebP by magic bytes),
  `/photos/{code}/{id}`, `/media/{video_id}.mp4`.
- WebSocket `/ws/{code}?role=display|guest`. Phone → server: `add`, `remove`,
  `move`, `skip`, `play`, `pause`, `restart`, `volume`, `retry`, `photo_skip`,
  `photo_remove`. Screen only: `ended`, `player`, `position`. Server → client:
  `state` (full snapshot) and `command`. Close code 4404 = room gone.
- Data on `/data` (owned by uid 1000, the container user): `songs/<video_id>.mp4`,
  `songs/.tmp/` (downloads in progress), `photos/<CODE>/<photo_id>`, `tmp/`
  (uploads in progress).
- Tables: `rooms`, `queue_items`, `videos`, `photos`.

## Commands (from `karaoke/`)

```bash
uv sync
make dev                          # Postgres container karaoke-db on :55432 + app on :8000
make format                       # ruff format + fix
make check-format typecheck test  # must pass before any deploy (same as CI)
uv run pytest -m integration -v   # real YouTube search + download (slow, network)
```

Env: `KARAOKE_DATABASE_URL` or `KARAOKE_DB_USER/PASSWORD/HOST/PORT/NAME`,
`KARAOKE_DATA_DIR`, `KARAOKE_PUBLIC_URL` (QR base URL), `KARAOKE_CHANNELS`,
`KARAOKE_CACHE_MAX_GB`, `KARAOKE_MAX_HEIGHT`, `KARAOKE_HOST_PASSWORD` (empty =
anyone can create a room), `PORT`, `LOG_LEVEL`.

## Testing

- Unit tests need Postgres: `KARAOKE_TEST_DATABASE_URL` (CI uses a service
  container), otherwise `conftest.py` starts a throwaway `postgres:18-alpine` with
  docker. Tables are dropped before each test.
- The `client` fixture stubs `hub.kick_download`: unit tests never reach YouTube.
- Put logic in `rooms.py`/`photos.py`/`search.py` and test it there; test wiring
  in `test_app.py` through HTTP and WebSockets. `test_two_replicas_share_rooms`
  runs two app instances on one database: extend it for cross-replica behaviour.
- `-m integration` hits YouTube; run it after touching search or download code.

## Deploy (owner's workflow)

Iterate without a git round-trip; do not commit or push unless asked.

```bash
make check-format typecheck test
docker buildx build --platform linux/amd64 -t ghcr.io/dixneuf19/karaoke:main --push .
kubectl -n karaoke rollout restart deploy/karaoke
```

When chart values change: `make deploy` in the gitops chart directory
(`helm upgrade --install -n karaoke karaoke . -f values.yaml`).
The image is amd64-only for now (no arm64 emulation on the laptop), hence the
amd64 nodeSelector; `.github/workflows/karaoke-docker.yml` builds amd64+arm64 and
pushes `:main` on every push to main, overwriting a local push of the same tag.

## Gitops (`~/codes/brassberry/brassberry-gitops`)

- `gitops/karaoke/karaoke/`: base-helm-chart values (2 replicas, 1Gi limit: two
  parallel downloads with yt-dlp + deno + ffmpeg OOM-killed a 512Mi pod,
  Traefik ingress karaoke.dixneuf19.fr, `karaoke-songs` nfs-client RWX PVC on
  `/data`), `templates/pdb.yaml` (minAvailable 1), `templates/database-credentials.yaml`
  (ESO SecretStore copying the DB credentials from namespace `playground-db`).
- `gitops/playground-db/playground-db/`: shared CNPG cluster `playground`
  (host `playground-rw.playground-db.svc`), one instance on k8s-worker-1,
  barman-cloud backups to S3. Each entry of `databases:` gets a generated password,
  a role and a database.
- ArgoCD Application `karaoke` is in `gitops/argocd/apps/values.yaml` (defaults:
  automated sync, selfHeal, prune); Image Updater tracks the `:main` digest in
  `gitops/argocd/argocd-image-updater/templates/image-updaters.yaml`. A manual
  helm upgrade that differs from git main can be reverted by ArgoCD.

## Key decisions

- **Postgres + LISTEN/NOTIFY** for multi-replica state: the playground DB already
  exists, no Redis, no sticky sessions; HA validated by killing a pod.
- **Single CNPG instance**: simpler, no HA; S3 backups cover data loss.
- **Download, don't stream**: smooth playback on the TV and a reusable cache;
  H.264/AAC MP4 plays everywhere, including old smart TVs. yt-dlp is a nightly
  (stable lags YouTube changes). The image ships deno (pinned, copied from
  `denoland/deno:bin-*`) for YouTube's JS challenges: it is the runtime yt-dlp
  enables by default because it runs that code without file or network access.
- **Container runs as uid 1000**, not root.
- **Songs still downloading keep their place**: `advance()` starts the first
  ready song; the waiting one plays as soon as it is ready.
- **No room password** in prod (owner's choice); `KARAOKE_HOST_PASSWORD` exists.
