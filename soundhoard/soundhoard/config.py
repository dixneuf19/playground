import os

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_ALLOWED_USERS: set[int] = {
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip()
}

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/music/SoundHoard")

NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "")
NAVIDROME_USER = os.environ.get("NAVIDROME_USER", "")
NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "")
