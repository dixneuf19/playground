# SoundHoard

Telegram bot that downloads YouTube audio and saves it to a Navidrome-compatible music library.

## Flow

```
Telegram message (YouTube URL) → validate → yt-dlp (extract MP3) → music folder → Navidrome
```

## Usage

Send a YouTube URL to the bot. It downloads the audio as MP3 with embedded metadata and thumbnail, saves it to the configured directory, and triggers a Navidrome rescan.

Duplicate URLs are detected and skipped (idempotent). If the file was deleted from disk, re-sending the URL will re-download it.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USERS` | No | Comma-separated Telegram user IDs |
| `DOWNLOAD_DIR` | No | Output directory (default: `/music/SoundHoard`) |
| `NAVIDROME_URL` | No | Navidrome base URL for triggering rescan |
| `NAVIDROME_USER` | No | Navidrome admin username |
| `NAVIDROME_PASSWORD` | No | Navidrome admin password |

## yt-dlp version

`yt-dlp` is pinned to a nightly build, not a stable release. YouTube regularly breaks
player clients, and fixes land on master months before the next stable tag. If downloads
start failing with `HTTP Error 403: Forbidden`, bump the pin in `pyproject.toml` to the
latest nightly on PyPI and re-run `uv lock`.

## Run locally

```bash
export TELEGRAM_BOT_TOKEN="your-token"
export DOWNLOAD_DIR="./downloads"
pip install .
soundhoard
```

## Docker

```bash
docker build -t soundhoard .
docker run -e TELEGRAM_BOT_TOKEN="your-token" -v ./downloads:/music/SoundHoard soundhoard
```
