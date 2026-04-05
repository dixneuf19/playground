import hashlib
import logging
import urllib.parse

import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from .config import (
    DOWNLOAD_DIR,
    NAVIDROME_PASSWORD,
    NAVIDROME_URL,
    NAVIDROME_USER,
    TELEGRAM_ALLOWED_USERS,
    TELEGRAM_BOT_TOKEN,
)
from .downloader import DownloadRegistry, download_audio, extract_video_id

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

registry = DownloadRegistry(DOWNLOAD_DIR)


def is_allowed(user_id: int) -> bool:
    return not TELEGRAM_ALLOWED_USERS or user_id in TELEGRAM_ALLOWED_USERS


async def trigger_navidrome_scan() -> None:
    if not NAVIDROME_URL:
        return
    try:
        # Subsonic API uses MD5 token auth
        import secrets

        salt = secrets.token_hex(8)
        token = hashlib.md5((NAVIDROME_PASSWORD + salt).encode()).hexdigest()
        params = {
            "u": NAVIDROME_USER,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "soundhoard",
        }
        url = f"{NAVIDROME_URL}/rest/startScan"
        async with httpx.AsyncClient() as client:
            await client.get(url, params=params)
        logger.info("Triggered Navidrome rescan")
    except Exception:
        logger.exception("Failed to trigger Navidrome rescan")


async def handle_message(update: Update, context) -> None:
    message = update.message
    if not message or not message.text:
        return

    if not is_allowed(message.from_user.id):
        await message.reply_text("You are not authorized to use this bot.")
        return

    video_id = extract_video_id(message.text)
    if not video_id:
        return  # Silently ignore non-YouTube messages

    # Idempotency check
    existing_title = registry.check(video_id)
    if existing_title:
        await message.reply_text(f"Already in library: {existing_title}")
        return

    url = message.text.strip()
    reply = await message.reply_text("Downloading...")

    try:
        filename, title = download_audio(url, DOWNLOAD_DIR)
        registry.register(video_id, filename, title)
        await reply.edit_text(f"Done: {title}")
        await trigger_navidrome_scan()
    except Exception as e:
        logger.exception("Download failed for %s", url)
        await reply.edit_text(f"Failed: {e}")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("SoundHoard bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
