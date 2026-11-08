# CLAUDE.md

Monorepo for small projects, scripts, and experiments.

## Projects

### soundhoard

Telegram bot that downloads YouTube audio and saves it to a Navidrome music library.

**Common commands (run from `soundhoard/`):**

```bash
uv sync                          # Install dependencies
make format                      # Format + lint fix
make check-format                # Check formatting + linting
make typecheck                   # Type check with ty
make test                        # Unit tests (fast, no network)
uv run pytest -m integration     # Integration test (downloads from YouTube, slow)
```

**Before finalizing a PR**, run the integration test to verify the download pipeline (yt-dlp → mp3 → metadata → cleanup) is not broken:

```bash
cd soundhoard && uv run pytest -m integration -v
```

### karaoke

Karaoke party web app: a big screen creates a room and plays the queue, phones join by QR code. FastAPI + WebSockets, Postgres for shared state (runs 2 replicas), Jinja + htmx phone page, plain JS screen page (no build step), yt-dlp for search (KaraFun YouTube channels first) and downloads. **Read `karaoke/AGENTS.md` before changing it.**

**Common commands (run from `karaoke/`):**

```bash
uv sync                          # Install dependencies
make dev                         # Postgres container + app on :8000 (needs docker)
make format                      # Format + lint fix
make check-format                # Check formatting + linting
make typecheck                   # Type check with ty
make test                        # Unit tests (no network; start a Postgres container)
uv run pytest -m integration     # Real YouTube search + download (slow)
```

### vitry-auberge-bot

Telegram bot reminding the Vitry flatmates of their chores. The schedule lives in `vitry_auberge_bot/chores.py`.

**Common commands (run from `vitry-auberge-bot/`):**

```bash
uv sync             # Install dependencies
make dev            # Run the bot locally (needs BOT_TELEGRAM_TOKEN and CHAT_ID in .env)
make format         # Format + lint fix
make check-format   # Check formatting + linting
make typecheck      # Type check with ty
make test           # Unit tests
```

### cluedo-vitry

Static prop website (Sam Marsalis's music blog) for the Vitry murder party, served by a standard library `server.py` with a fake admin login (`ADMIN_PASSWORD`). Deployed by hand with `kubectl apply` from `cluedo-vitry/k8s/` on the homelab cluster, not through brassberry-gitops.

**Common commands (run from `cluedo-vitry/`):**

```bash
uv sync             # Install dependencies
make dev            # Serve the site on :8000
make test           # Smoke test: start server.py and fetch pages
```
