# CLAUDE.md

Monorepo for small projects, scripts, and experiments.

Read `AGENTS.md` before adding a project: @AGENTS.md

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

Telegram bot reminding the Vitry flatmates of their chores. The schedule lives in `vitry_auberge_bot/chores.py`. La Fourche deliveries (optional, Postgres state) live in `vitry_auberge_bot/lafourche.py`.

**Common commands (run from `vitry-auberge-bot/`):**

```bash
uv sync             # Install dependencies
make dev            # Run the bot locally (needs BOT_TELEGRAM_TOKEN and CHAT_ID in .env)
make format         # Format + lint fix
make check-format   # Check formatting + linting
make typecheck      # Type check with ty
make test           # Unit tests
```

### sfr-mms-bot

Telegram bot: forward it the SFR SMS announcing an undelivered MMS, it logs in on vosmms.com and sends the MMS parts back. SMS parsing lives in `sfr_mms_bot/parser.py`, the vosmms.com client in `sfr_mms_bot/vosmms.py`. The user allowlist fails closed. Never commit a real SMS, phone number or password, not even in tests.

**Common commands (run from `sfr-mms-bot/`):**

```bash
uv sync             # Install dependencies
make dev            # Run the bot locally (needs TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in .env)
make format         # Format + lint fix
make check-format   # Check formatting + linting
make typecheck      # Type check with ty
make test           # Unit tests (no network)
```

### cluedo-vitry

Static prop website (Sam Marsalis's music blog) for the Vitry murder party, served by a standard library `server.py` with a fake admin login (`ADMIN_PASSWORD`). Deployed by hand with `kubectl apply` from `cluedo-vitry/k8s/` on the homelab cluster, not through brassberry-gitops.

**Common commands (run from `cluedo-vitry/`):**

```bash
uv sync             # Install dependencies
make dev            # Serve the site on :8000
make test           # Smoke test: start server.py and fetch pages
```
