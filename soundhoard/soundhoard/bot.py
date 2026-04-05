import logging
import re

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from .config import (
    DOWNLOAD_DIR,
    TELEGRAM_ALLOWED_USERS,
    TELEGRAM_BOT_TOKEN,
)
from .downloader import DownloadRegistry, download_audio, extract_info

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

registry = DownloadRegistry(DOWNLOAD_DIR)

URL_RE = re.compile(r"https?://\S+")


def is_allowed(user_id: int) -> bool:
    return not TELEGRAM_ALLOWED_USERS or user_id in TELEGRAM_ALLOWED_USERS


async def handle_message(update: Update, _context: object) -> None:
    message = update.message
    if not message or not message.text:
        return

    if not message.from_user or not is_allowed(message.from_user.id):
        await message.reply_text("You are not authorized to use this bot.")
        return

    # Extract first URL from message
    url_match = URL_RE.search(message.text)
    if not url_match:
        return
    url = url_match.group(0)

    reply = await message.reply_text("Extracting info...")

    try:
        tracks = extract_info(url)
    except Exception as e:
        logger.exception("Failed to extract info from %s", url)
        await reply.edit_text(f"Not a supported URL: {e}")
        return

    if not tracks:
        await reply.edit_text("No tracks found at this URL.")
        return

    # Filter out already downloaded tracks
    new_tracks = []
    skipped = []
    for track in tracks:
        existing = registry.check(track.video_id)
        if existing:
            skipped.append(existing)
        else:
            new_tracks.append(track)

    if not new_tracks:
        titles = ", ".join(skipped)
        await reply.edit_text(f"Already in library: {titles}")
        return

    if skipped:
        await reply.edit_text(
            f"Downloading {len(new_tracks)} track(s) ({len(skipped)} already in library)..."
        )
    else:
        await reply.edit_text(f"Downloading {len(new_tracks)} track(s)...")

    done = []
    failed = []
    for track in new_tracks:
        try:
            filename = download_audio(url, DOWNLOAD_DIR, video_id=track.video_id)
            registry.register(track.video_id, filename, track.title)
            done.append(track.title)
        except Exception:
            logger.exception("Download failed for %s (%s)", track.title, track.video_id)
            failed.append(track.title)

    parts = []
    if done:
        parts.append("Done: " + ", ".join(done))
    if failed:
        parts.append("Failed: " + ", ".join(failed))
    await reply.edit_text("\n".join(parts))


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("SoundHoard bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
