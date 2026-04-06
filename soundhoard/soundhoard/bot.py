import logging
import re

from telegram import Update
from telegram.ext import Application, MessageHandler, filters
from telegram.helpers import escape_markdown

from .config import (
    DOWNLOAD_DIR,
    TELEGRAM_ALLOWED_USERS,
    TELEGRAM_BOT_TOKEN,
)
from .downloader import DownloadRegistry, download_with_retry, extract_info

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

    logger.info("Received URL: %s", url)
    reply = await message.reply_text("Extracting info...")

    try:
        tracks = extract_info(url)
    except Exception as e:
        logger.exception("Failed to extract info from %s", url)
        await reply.edit_text(f"Not a supported URL: {e}")
        return

    if not tracks:
        logger.info("No tracks found for %s", url)
        await reply.edit_text("No tracks found at this URL.")
        return

    logger.info("Found %d track(s) for %s", len(tracks), url)

    # Filter out already downloaded tracks
    new_tracks = []
    skipped = []
    for track in tracks:
        existing = registry.check(track.video_id)
        if existing:
            skipped.append(existing)
        else:
            new_tracks.append(track)

    # Summary of what was found
    total_found = len(tracks)
    total_new = len(new_tracks)
    total_skipped = len(skipped)

    is_playlist = total_found > 1

    if not new_tracks:
        await reply.edit_text(f"Found {total_found} track(s), all already in library.")
        return

    if is_playlist:
        skip_info = f", {total_skipped} already in library" if total_skipped else ""
        await reply.edit_text(
            f"Found {total_found} track(s){skip_info}. Downloading {total_new}..."
        )

    done = []
    failed = []
    for i, track in enumerate(new_tracks, 1):
        progress = f"[{i}/{total_new}] " if is_playlist else ""
        escaped_title = escape_markdown(track.title)
        await reply.edit_text(
            f"{progress}Downloading - _{escaped_title}_",
            parse_mode="Markdown",
        )
        try:
            logger.info("Downloading %s (%s)", track.title, track.video_id)
            filename = download_with_retry(track.video_id, DOWNLOAD_DIR)
            registry.register(track.video_id, filename, track.title)
            logger.info("Saved %s", filename)
            done.append(track.title)
        except Exception:
            logger.exception("Download failed for %s (%s)", track.title, track.video_id)
            failed.append(track.title)

    if is_playlist:
        parts = []
        if done:
            parts.append(f"Done ({len(done)}/{total_new})")
        if failed:
            parts.append(f"Failed ({len(failed)}): " + ", ".join(failed))
        await reply.edit_text("\n".join(parts))
    else:
        if done:
            escaped = escape_markdown(new_tracks[0].title)
            await reply.edit_text(f"Done - _{escaped}_", parse_mode="Markdown")
        else:
            await reply.edit_text(f"Failed - {new_tracks[0].title}")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("SoundHoard bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
