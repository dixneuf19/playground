import asyncio
import logging

from telegram import Message, Update
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from sfr_mms_bot import config
from sfr_mms_bot.parser import ParseError, parse_sms
from sfr_mms_bot.vosmms import LoginError, Part, fetch_mms

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def send_part(message: Message, part: Part, caption: str) -> None:
    if part.content_type == "text/plain":
        try:
            text = part.data.decode("utf-8")
        except UnicodeDecodeError:
            text = part.data.decode("iso-8859-15")
        if text.strip():
            await message.reply_text(text)
        return
    try:
        if part.content_type in ("image/jpeg", "image/png", "image/webp"):
            await message.reply_photo(part.data, caption=caption, filename=part.filename)
            return
        if part.content_type == "image/gif":
            await message.reply_animation(part.data, caption=caption, filename=part.filename)
            return
        if part.content_type in ("video/mp4", "video/quicktime"):
            await message.reply_video(part.data, caption=caption, filename=part.filename)
            return
    except BadRequest as e:
        logger.warning("Telegram refused a %s as media (%s), sending it as a file", part.content_type, e)
    await message.reply_document(part.data, caption=caption, filename=part.filename)


async def handle_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    try:
        notice = parse_sms(message.text)
    except ParseError:
        await message.reply_text("Ce n'est pas un SMS « Info SFR » de MMS. Transfère-moi le SMS en entier.")
        return

    logger.info("Fetching an MMS")
    status = await message.reply_text(f"📨 Je récupère le MMS de {notice.sender}…")
    try:
        mms = await asyncio.to_thread(fetch_mms, notice)
    except LoginError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception:
        logger.exception("Fetching the MMS failed")
        await status.edit_text("❌ Échec de la récupération sur vosmms.com, réessaie plus tard.")
        return

    logger.info("Got %d part(s): %s", len(mms.parts), ", ".join(p.content_type for p in mms.parts))
    if not mms.parts and not mms.text:
        await status.edit_text("🤷 Connecté à vosmms.com, mais ce MMS est vide.")
        return
    await status.edit_text(f"📨 MMS de {notice.sender}")
    if mms.text:
        await message.reply_text(mms.text)
    for part in mms.parts:
        await send_part(message, part, caption=f"De {notice.sender}")


def main() -> None:
    token, allowed_users = config.load()
    app = Application.builder().token(token).build()
    # Messages from anyone else never reach the handler, so they get no reply at all.
    app.add_handler(
        MessageHandler(filters.User(user_id=allowed_users) & filters.TEXT & ~filters.COMMAND, handle_message)
    )
    logger.info("sfr-mms-bot started for %d user(s)", len(allowed_users))
    app.run_polling()


if __name__ == "__main__":
    main()
