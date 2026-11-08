# AGENTS.md — karaoke

Guide for coding agents. A KaraFun-like karaoke night for the owner's homelab, used
by French-speaking friends, live at https://karaoke.dixneuf19.fr.

TODO and ideas: GitHub issues labelled `karaoke` in dixneuf19/playground
(`gh issue list --label karaoke`). Close or update them as work lands.

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
- **The screen doesn't show who sent a photo** (a product choice, not a security
  property). A guest is known by the name confirmed for that room, in the
  `karaoke_name_<CODE>` cookie: the phone page lists and cancels the pending
  photos uploaded under that name, and tags their songs "moi". Same-name guests
  share them (issue #55).
- **A name is confirmed per room.** Entering a room opens the name sheet,
  prefilled with the last name used anywhere (`karaoke_name` cookie, a
  suggestion only); the guest confirms or changes it. Tapping the name in the
  header changes it for this room.
- **Photo fairness, not FIFO** (`photos.PhotoQueue`): next photo comes from the
  uploader shown least recently; never-shown uploaders first; ties by upload order.
  One photo at a time, 10 s, tilted instant-photo print with washi tape; rotating
  corners during a song, centred between songs.
- **Resume after a screen reload**: the screen reports its position every 5 s; a
  reloaded screen resumes mid-song with a 3 s rewind and skips the "Au tour de…"
  interstitial (6 s, only for fresh songs).
- **Popular songs as inspiration**: before any search, the phone lists the 20
  songs sung in the most rooms over the last 30 days (ties: most recent), with
  the usual one-tap add. A play is recorded in `plays` when a song becomes
  current (`Store._save`), skipped or not; a room counts once per song, so
  add + skip loops don't climb. Titles shown to every room come from YouTube
  (`videos.title`, artist via `search.split_title`), never from a phone's
  `add`. The leader's prune drops rows older than 30 days.
- Out of scope: lights; scraping or pirating KaraFun's paid catalogue.

## Code map (`karaoke/karaoke/`)

| File | Role |
|---|---|
| `rooms.py`, `photos.py` | Pure state and logic, no I/O (Room, QueueItem, PhotoQueue, photo slot timing). Unit-tested directly. Methods that may do nothing return a bool. |
| `messages.py` | Pydantic models of every phone/screen message, one per `type`. All input validation lives here. |
| `actions.py` | `Actions.handle`: applies a message to a room inside `room_tx` (a `match` on the message class). |
| `db.py` | `Store`: Postgres via asyncpg. `MIGRATIONS`, `room_tx`, `load_room`, videos/photos/plays tables, NOTIFY. |
| `hub.py` | This replica's sockets (`Client`), the LISTEN connection and the per-room push loop. |
| `worker.py` | Leader-only background work: downloads, photo rotation, repair, pruning. Owns the `Leadership`. |
| `leader.py` | Leader election with a Postgres advisory lock held on a dedicated connection. |
| `api.py` | JSON API, photo upload, media files, the `/ws/{code}` WebSocket. |
| `pages.py`, `views.py` | HTML pages (Jinja) and the htmx endpoints of the phone page. |
| `context.py` | `Services` (built once in `app.start`) and the `Svc`/`GuestName` dependencies. |
| `search.py` | yt-dlp flat search (channels / all YouTube / single link), title parsing, ranking, `Searcher` (cache + concurrency). |
| `library.py`, `photo_files.py` | Video files (yt-dlp download, ≤720p H.264 + AAC) and photo files on `/data`. |
| `cancelled.py` | Cancelled artists, matched on whole normalized words. |
| `config.py` | `Settings` from `KARAOKE_*` env vars. |
| `app.py` | Wiring only: builds the services, includes the routers. |
| `templates/` | `guest.html` (+ `_guest_state.html` fragments, `_guest_push.html`, `_results.html`), `display.html`, `index.html`. |
| `static/` | `guest.js`/`guest.css`, `display.js`/`display.css`, `common.js` (screen socket + `esc`), `app.css`. htmx 4 (+ its `hx-ws` extension) and SortableJS come from jsDelivr in `guest.html`, pinned with an integrity hash. No build step. |
| `scripts/sri.py` | Recomputes (`make sri`) or checks (`make check-sri`, in CI) the integrity hashes of CDN scripts in the templates. |

## Frontend

- **Phone page = htmx 4** (not 2: events are `htmx:phase:action`, no `hx-ext`,
  attributes don't inherit, morphing is built in; see the htmx upgrade guide).
  The server renders it with Jinja (autoescaped) for the guest in the cookie.
  Each `_guest_state.html` macro renders one region with an id; after every
  change the hub renders them all (`_guest_push.html`, once per guest name)
  and pushes the HTML over the WebSocket (`hx-ws:connect`, `?view=html`); each
  element carries `hx-swap-oob="outerMorph"`, so scroll, focus and drags
  survive. 4xx/5xx responses are not swapped (`noSwap` in the `htmx-config`
  meta tag): `guest.js` shows their JSON `detail` as a toast. Buttons
  `hx-post` to `/r/{code}/do` (form fields = a `messages` model; `singer` and
  `uploader` always come from the cookie) and get the new state by push.
  `hx-confirm` opens the app's own sheet; toasts come from `HX-Trigger`.
  Search is `GET /r/{code}/search` returning `_results.html`.
- `guest.js` only does what needs the browser: tabs, the name cookie, the
  confirm sheet, toasts, drag and drop (pushes are held during a drag), photo
  resizing before upload.
- **Screen page = plain JS** (`display.js`): it is a video player state machine,
  fed with JSON states and commands. Keep it that way.
- Pages reference static files through `static('name')`, which appends a
  content hash: no stale JS after a deploy.
- CDN libraries are pinned to an exact version with a `sha384` integrity hash.
  Renovate bumps the versions (custom regex manager on jsDelivr URLs in
  `renovate.json`); CI's `make check-sri` then fails on its PR, which blocks
  the automerge, until someone runs `make sri` on that branch and pushes the
  new hashes. Nothing commits them automatically. By hand: change the version, `make sri`.

## How multi-replica state works

Prod runs 2 replicas. Everything a replica knows lives in Postgres or on the shared
`/data` volume; a process only keeps its open sockets.

1. A change goes through `Store.room_tx(code)`: `SELECT … FOR UPDATE` on the room
   row, load the whole aggregate (room, queue, photos), mutate it with the pure
   methods, save it in the same transaction (queue rows are deleted and rewritten).
2. On commit, `pg_notify('karaoke', {"room": code})` reaches every replica's
   LISTEN connection; each re-reads the snapshot (one REPEATABLE READ read) and
   pushes `{"type": "state"}` to its local sockets. One push loop per room
   (`Hub.push_state`): pushes stay in order and bursts coalesce. JSON clients
   (screens) share one message; htmx phones get HTML rendered once per name. Sends go to all
   sockets in parallel with a 5 s timeout; a socket that doesn't keep up is closed
   (close code 1013: the page reconnects and gets the full state), so one frozen
   phone never holds up the TV. Display commands (`play`/`pause`/`restart`/`volume`) are sent
   as `{"room", "command"}` via `tx.command()` and forwarded only to screens.
3. **One leader runs the background work** (`leader.py`). Every replica serves
   pages and phone actions; the leader also runs photo rotation (0.5 s), the
   download loop, a repair pass (15 s) and pruning (10 min). Leadership is a
   session-level `pg_try_advisory_lock` on a connection of its own: when the pod
   stops or dies the connection closes and Postgres releases the lock; followers
   retry every 5 s (TCP keepalives on that connection catch a vanished node in
   ~30 s). The first replica up takes it during startup. A new leader puts
   `downloading` videos back to `pending` and starts them over.
4. Downloads: a replica that queues a new video sets `tx.wake_downloads`, which
   NOTIFIES `{"downloads": true}` on commit (after the song is saved); the leader's loop
   wakes, picks queued `pending` videos (also scans every 5 s), 2 at a time:
   `pending → downloading → ready|error`. A download writes into
   `songs/.tmp/<worker>-<id>/` and is renamed into `songs/` when complete, so the
   cache only ever holds whole files, even if a demoted leader's yt-dlp thread
   finishes late. LRU eviction (`KARAOKE_CACHE_MAX_GB`, 20) never evicts a video
   still in a queue (re-checked in the `DELETE`, since followers add songs
   concurrently). The repair pass recreates video rows missing for queued songs,
   re-downloads lost files and starts a ready song in idle rooms.
5. Position reports (`position`) are a plain `UPDATE` without lock or NOTIFY:
   they are frequent and nobody needs a push for them. They only apply while
   `rooms.current_item_id` is the reported song.
6. The screen follows `state.state` for play/pause, not only the commands: a
   command lost during a reconnect is fixed by the next state push.

Invariants to keep:

- Never keep state only in process memory (the `downloading` set and photo-file
  deletion timers are local optimizations, not state).
- Background work belongs on the leader (`Worker._lead`), never in a loop every
  replica runs. Actions from phones run on any replica, inside `room_tx`.
- Room changes from the leader and from phones meet on the room row lock
  (`room_tx`); keep it that way rather than adding other locks.
- Mutate rooms only inside `room_tx`, with logic in `rooms.py`/`photos.py`;
  `actions.py`/`worker.py` only call those methods and set `tx.save` from their
  result, so a no-op is neither saved nor pushed.
- A new message = a model in `messages.py` (+ `screen_only` if needed) and a
  `case` in `Actions._apply`. Don't parse raw dicts anywhere else.
- Room times (`photo_until`, `position_at`) come from the app's clock
  (`time.time()`), never from Postgres `now()`.
- Keep NOTIFY payloads tiny (room code + optional command; Postgres caps them at
  8000 bytes). Replicas re-read state from the DB, never from the payload.
- Screen-only messages (`ended`, `player`, `position`) are gated by `is_display`;
  commands go only to screens.
- Schema changes = a new entry at the end of `db.MIGRATIONS` (never edit an
  applied one). Each runs once, recorded in `schema_migrations`, under an
  advisory lock at startup. A rolling update runs the previous release against
  the new schema: add things, or drop only what the previous release no longer
  reads.
- Frontend: render HTML in Jinja templates (autoescaped). In `display.js`, any
  server string put into `innerHTML` goes through `esc()`, or use `textContent`.
- Public site, no login: validate what phones send (`messages.py`). Video ids
  must match `[A-Za-z0-9_-]{11}` (`library.VIDEO_ID`: they become file paths and
  URLs); thumbnails are built server-side; queue capped at 200; WebSocket messages
  at 64 KiB (`__main__.py`); photos streamed to `/data/tmp` with a running size
  check (10 MB), then moved in; 30 pending photos per name, 300 per room.
- yt-dlp traffic comes from the home IP, so keep it modest: searches need a live
  `room`, run 4 at a time per replica and are cached 10 min in memory per replica.

## Protocol

- REST: `/healthz`, `/readyz` (DB ping), `POST /api/rooms`, `GET /api/rooms/{code}`,
  `/api/rooms/{code}/qr.svg`, `/api/search?room=&q=&everywhere=` (JSON, for
  phone pages opened before the htmx one), `POST /api/rooms/{code}/photos`
  (raw body, JPEG/PNG/WebP by magic bytes; uploader = `?uploader=` or the
  cookie), `/photos/{code}/{id}`, `/media/{video_id}.mp4`.
- Pages: `/`, `/display/{code}`, `/r/{code}`. htmx: `GET /r/{code}/search`,
  `GET /r/{code}/state` (the pushed fragments, on demand), `POST /r/{code}/do`
  (any phone message as form fields; 204, maybe `HX-Trigger: {"toast": …}`).
- WebSocket `/ws/{code}?role=display|guest&view=json|html`. Messages
  (`messages.py`) phone → server: `add`, `remove`, `move`, `skip`, `play`,
  `pause`, `restart`, `volume`, `retry`, `photo_skip`, `photo_remove`. Screen
  only: `ended`, `player`, `position`. Server → JSON clients: `state` (full
  snapshot) and `command` (screens only); → `view=html`: HTML fragments. Close
  codes: 4404 room gone, 1013 dropped for being slow (reconnect).
- Data on `/data` (owned by uid 1000, the container user): `songs/<video_id>.mp4`,
  `songs/.tmp/` (downloads in progress), `photos/<CODE>/<photo_id>`, `tmp/`
  (uploads in progress).
- Tables: `rooms`, `queue_items`, `videos`, `photos`, `plays`, `schema_migrations`.

## Commands (from `karaoke/`)

```bash
uv sync
make dev                          # Postgres container karaoke-db on :55432 + app on :8000
make format                       # ruff format + fix
make check-format typecheck test  # must pass before any deploy (same as CI)
make check-sri                    # CDN integrity hashes (network; also in CI)
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
- The `client` fixture sets `services(c).worker.download_videos = False`: unit tests never reach
  YouTube (`test_leader_downloads_a_queued_song_and_starts_it` fakes the
  download instead). `test_one_leader_and_a_quick_takeover` covers the election.
- Put logic in `rooms.py`/`photos.py`/`search.py` and test it there; messages in
  `test_messages.py`; wiring in `test_app.py` (JSON/WebSocket) and
  `test_pages.py` (htmx page: set the `karaoke_name_<CODE>` cookie URL-encoded). `test_two_replicas_share_rooms`
  runs two app instances on one database: extend it for cross-replica behaviour.
- `-m integration` hits YouTube; run it after touching search or download code.

## Deploy (gitops)

Everything is deployed by ArgoCD from `~/codes/brassberry/brassberry-gitops`
(k0s homelab cluster: arm64 Raspberry Pis + one amd64 worker):

- **App code**: merge to `main` here → `.github/workflows/karaoke-docker.yml`
  builds `ghcr.io/dixneuf19/karaoke:main` for amd64 + arm64 → ArgoCD Image
  Updater sees the new digest, commits it to the gitops repo
  (`gitops/karaoke/karaoke/.argocd-source-karaoke.yaml`) → ArgoCD rolls the pods.
- **Chart / config**: PR in the gitops repo. `gitops/karaoke/karaoke/` holds the
  base-helm-chart values (2 replicas, 1Gi limit: two parallel downloads
  OOM-killed 512Mi; Traefik ingress karaoke.dixneuf19.fr; `karaoke-data`
  RWX PVC on `/data`, storage class `nfs-jonbonas`: the NAS), `templates/pdb.yaml` and
  `templates/database-credentials.yaml` (ESO SecretStore copying the DB
  credentials from namespace `playground-db`).
- **Database**: `gitops/playground-db/playground-db/`, shared CNPG cluster
  `playground` (host `playground-rw.playground-db.svc`, one instance on
  k8s-worker-1, barman-cloud backups to S3). Each key of `databases:` gets a
  generated password, a role and a database.
- ArgoCD apps `karaoke` and `playground-db` are in `gitops/argocd/apps/values.yaml`
  (automated sync with selfHeal: a manual `helm upgrade` or `kubectl edit` is
  reverted). Image Updater config:
  `gitops/argocd/argocd-image-updater/templates/image-updaters.yaml`.

Quick iteration without a merge (owner's habit, don't commit or push unless
asked): push the image yourself, Image Updater picks up the new `:main` digest
within a few minutes. Pods run on any node, so the image must be multi-arch; an
amd64-only push breaks pods on the Pis. Building arm64 locally needs QEMU
emulation installed once (`docker run --privileged --rm tonistiigi/binfmt
--install arm64`).

```bash
make check-format typecheck test
docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/dixneuf19/karaoke:main --push .
```

## Key decisions

- **Postgres + LISTEN/NOTIFY** for multi-replica state: the playground DB already
  exists, no Redis, no sticky sessions; HA validated by killing a pod.
- **A leader instead of coordinating every replica**: download claims, heartbeats
  and per-job locks were the source of most multi-replica bugs. If the project
  grows, the next step is a dedicated worker Deployment (leader-only work) and
  frontend replicas, which this split already allows.
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
