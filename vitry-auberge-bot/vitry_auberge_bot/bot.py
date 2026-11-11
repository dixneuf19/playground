import html
import logging
import os
from datetime import date, datetime, timedelta
from typing import cast

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update, User
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

from vitry_auberge_bot import lafourche
from vitry_auberge_bot.chores import CHORES, CHORES_BY_ID, TZ, Chore, format_when, in_progress, upcoming
from vitry_auberge_bot.tracker import Occurrence, Tracker

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TELEGRAM_TOKEN = os.getenv("BOT_TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID") or 0)
LAFOURCHE_EMAIL = os.getenv("LAFOURCHE_EMAIL")
LAFOURCHE_PASSWORD = os.getenv("LAFOURCHE_PASSWORD", "")

REMINDER_PREFIXES = (
    "⏰ Rappel !",
    "😬 Toujours personne ?",
    "🚨 Dernier rappel !",
)

tracker = Tracker()
orders: lafourche.OrderSource | None = None
store: lafourche.Store | None = None


def done_keyboard(occurrence: Occurrence) -> InlineKeyboardMarkup:
    suffix = f"{occurrence.chore.id}:{occurrence.day.isoformat()}"
    buttons = [InlineKeyboardButton("✅ C'est fait", callback_data=f"done:{suffix}")]
    if occurrence.chore.skippable:
        buttons.append(InlineKeyboardButton("🙅 Pas besoin", callback_data=f"skip:{suffix}"))
    return InlineKeyboardMarkup([buttons])


def display_name(user: User | None) -> str:
    return user.first_name if user else "quelqu'un"


async def send_for(context: ContextTypes.DEFAULT_TYPE, occurrence: Occurrence, text: str) -> None:
    reply_to = None
    if occurrence.message_ids:
        reply_to = ReplyParameters(message_id=occurrence.message_ids[0], allow_sending_without_reply=True)
    message = await context.bot.send_message(
        CHAT_ID,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=done_keyboard(occurrence),
        reply_parameters=reply_to,
    )
    tracker.add_message(occurrence, message.message_id)


def schedule_reminders(job_queue: JobQueue, chore: Chore, day: date, now: datetime) -> None:
    for index, when in enumerate(chore.reminder_times(day)):
        if when > now:
            job_queue.run_once(send_reminder, when=when, data=(chore, day, index), name=f"{chore.id}:{day}:{index}")


async def start_chore(context: ContextTypes.DEFAULT_TYPE) -> None:
    assert context.job is not None
    chore = cast(Chore, context.job.data)
    now = datetime.now(TZ)
    if not chore.occurs_on(now.date()):
        return
    logger.info("Starting chore %s", chore.id)
    occurrence = tracker.get(chore, now.date())
    await send_for(context, occurrence, chore.message)
    assert context.job_queue is not None
    schedule_reminders(context.job_queue, chore, now.date(), now)


async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    assert context.job is not None
    chore, day, index = cast(tuple[Chore, date, int], context.job.data)
    occurrence = tracker.get(chore, day)
    if occurrence.done:
        return
    logger.info("Reminder %d for chore %s", index, chore.id)
    prefix = REMINDER_PREFIXES[min(index, len(REMINDER_PREFIXES) - 1)]
    await send_for(context, occurrence, f"{prefix} {chore.message}")


async def mark_done(
    context: ContextTypes.DEFAULT_TYPE, occurrence: Occurrence, who: str, skipped: bool = False
) -> None:
    if not tracker.acknowledge(occurrence, who, skipped):
        return
    logger.info("Chore %s %s by %s", occurrence.chore.id, "skipped" if skipped else "done", who)
    for message_id in occurrence.message_ids:
        try:
            await context.bot.edit_message_reply_markup(CHAT_ID, message_id, reply_markup=None)
        except TelegramError as error:
            logger.warning("Could not remove keyboard from message %d: %s", message_id, error)
    await context.bot.send_message(
        CHAT_ID,
        f"Ok {html.escape(who)} 👌" if skipped else f"Merci {html.escape(who)} 🙏",
        parse_mode=ParseMode.HTML,
        reply_parameters=ReplyParameters(message_id=occurrence.message_ids[0], allow_sending_without_reply=True)
        if occurrence.message_ids
        else None,
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query is not None and query.data is not None
    action, chore_id, day = query.data.split(":")
    occurrence = tracker.find((chore_id, date.fromisoformat(day)))
    if occurrence is None:
        await query.answer("Ce rappel est expiré 🤷")
        return
    if occurrence.done:
        await query.answer(
            f"{occurrence.done_by} a dit pas besoin" if occurrence.skipped else f"Déjà fait par {occurrence.done_by}"
        )
        return
    await query.answer()
    await mark_done(context, occurrence, display_name(query.from_user), skipped=action == "skip")


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction = update.message_reaction
    if reaction is None or not reaction.new_reaction:
        return
    occurrence = tracker.by_message(reaction.message_id)
    if occurrence is not None:
        await mark_done(context, occurrence, display_name(reaction.user))


async def on_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.reply_to_message is None:
        return
    occurrence = tracker.by_message(message.reply_to_message.message_id)
    if occurrence is not None:
        await mark_done(context, occurrence, display_name(message.from_user))


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_message is not None
    await update.effective_message.reply_text(
        "Je rappelle les corvées de la colloc 🏠\n\n"
        "Quand c'est fait, réagis à mon message, réponds-y ou clique sur ✅. "
        "Sinon je relance jusqu'à ce que quelqu'un s'en occupe !\n\n"
        "/prochains : les rappels des 7 prochains jours\n"
        "/chatid : l'identifiant de ce chat"
    )


async def show_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_message is not None and update.effective_chat is not None
    await update.effective_message.reply_text(f"chat id: {update.effective_chat.id}")


async def show_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_message is not None
    lines = [f"• {format_when(when)} : {chore.label}" for when, chore in upcoming(datetime.now(TZ))]
    await update.effective_message.reply_text("\n".join(lines) or "Rien de prévu 🎉")


async def resume_in_progress(application: Application) -> None:
    """After a restart, keep nagging for chores whose reminder window is still open."""
    now = datetime.now(TZ)
    job_queue = application.job_queue
    assert job_queue is not None
    for day, chore in in_progress(now):
        logger.info("Resuming reminders for chore %s of %s", chore.id, day)
        schedule_reminders(job_queue, chore, day, now)


async def poll_orders(context: ContextTypes.DEFAULT_TYPE) -> None:
    assert orders is not None and store is not None
    now = datetime.now(TZ)
    try:
        fetched = await orders.fetch_orders()
    except Exception:
        logger.exception("Could not fetch La Fourche orders")
        return
    changes = lafourche.compare(await store.known(now), fetched, now)
    # Sent before being recorded: a failed send is retried at the next poll
    for order in changes.new:
        logger.info("New La Fourche order %s", order.id)
        await context.bot.send_message(CHAT_ID, lafourche.new_order_message(order), parse_mode=ParseMode.HTML)
        await store.add(order)
    for previous, order in changes.rescheduled:
        logger.info("La Fourche order %s rescheduled", order.id)
        await context.bot.send_message(
            CHAT_ID, lafourche.rescheduled_message(previous, order), parse_mode=ParseMode.HTML
        )
        await store.reschedule(order)


async def remind_orders(context: ContextTypes.DEFAULT_TYPE) -> None:
    assert store is not None
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    for order in await store.due_reminders(tomorrow):
        logger.info("Reminder for La Fourche order %s", order.id)
        await context.bot.send_message(CHAT_ID, lafourche.reminder_message(order), parse_mode=ParseMode.HTML)
        await store.mark_reminded(order)


async def post_init(application: Application) -> None:
    global orders, store
    await resume_in_progress(application)
    if not LAFOURCHE_EMAIL:
        logger.info("LAFOURCHE_EMAIL is not set: La Fourche deliveries are not followed")
        return
    orders = lafourche.LaFourcheClient(LAFOURCHE_EMAIL, LAFOURCHE_PASSWORD)
    store = await lafourche.Store.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT") or 5432),
        database=os.getenv("DB_NAME", "vitry_auberge_bot"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    job_queue = application.job_queue
    assert job_queue is not None
    for index, when in enumerate(lafourche.POLL_TIMES):
        job_queue.run_daily(poll_orders, time=when, name=f"lafourche-poll-{index}")
    job_queue.run_daily(remind_orders, time=lafourche.REMINDER_TIME, name="lafourche-reminder")
    # Catch up on what was missed while the bot was down
    job_queue.run_once(poll_orders, when=0, name="lafourche-poll-startup")


async def post_shutdown(application: Application) -> None:
    if store is not None:
        await store.close()


def main() -> None:
    if not BOT_TELEGRAM_TOKEN:
        raise RuntimeError("BOT_TELEGRAM_TOKEN environment variable is required")
    if not CHAT_ID:
        logger.warning("CHAT_ID is not set: only /chatid and /help will be useful")

    application = (
        Application.builder().token(BOT_TELEGRAM_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    )
    job_queue = application.job_queue
    assert job_queue is not None

    for chore in CHORES:
        job_queue.run_daily(start_chore, time=chore.start, data=chore, name=chore.id)

    group = filters.Chat(CHAT_ID)
    application.add_handler(CommandHandler(["help", "start"], show_help))
    application.add_handler(CommandHandler("chatid", show_chat_id))
    application.add_handler(CommandHandler("prochains", show_upcoming))
    application.add_handler(CallbackQueryHandler(on_button, pattern=r"^(done|skip):"))
    application.add_handler(MessageReactionHandler(on_reaction, chat_id=CHAT_ID))
    application.add_handler(MessageHandler(group & filters.REPLY, on_reply))

    logger.info("Scheduled chores: %s", ", ".join(CHORES_BY_ID))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
