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
