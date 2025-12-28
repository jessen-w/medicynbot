import os
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Defaults,
)

# ----------------------------
# CONFIG
# ----------------------------
TZ = ZoneInfo("Asia/Jakarta")

FOOD_TIME = time(hour=10, minute=0, tzinfo=TZ)
MED_MORNING_TIME = time(hour=11, minute=0, tzinfo=TZ)
MED_EVENING_TIME = time(hour=18, minute=0, tzinfo=TZ)

NAG_EVERY = timedelta(minutes=30)

# Optional: set this to YOUR chat id to get alerts / debug
# (Leave empty if you don't want it)
ADMIN_CHAT_ID_ENV = "ADMIN_CHAT_ID"

# Cynthia persistence method (recommended on Railway):
# Set CYNTHIA_CHAT_ID as an environment variable.
CYNTHIA_CHAT_ID_ENV = "CYNTHIA_CHAT_ID"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("medicynbot")


# ----------------------------
# ENV + RUNTIME STORAGE
# ----------------------------
def get_admin_chat_id() -> Optional[int]:
    v = os.getenv(ADMIN_CHAT_ID_ENV)
    return int(v) if v and v.isdigit() else None


def get_cynthia_chat_id(context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Priority:
    1) Environment variable CYNTHIA_CHAT_ID (persists on Railway)
    2) In-memory app.bot_data (works until the process restarts)
    """
    env = os.getenv(CYNTHIA_CHAT_ID_ENV)
    if env and env.isdigit():
        return int(env)

    mem = context.application.bot_data.get("cynthia_chat_id")
    return int(mem) if mem is not None else None


def set_cynthia_chat_id_runtime(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    context.application.bot_data["cynthia_chat_id"] = chat_id


def today_key(now: datetime) -> str:
    return now.astimezone(TZ).strftime("%Y-%m-%d")


def nag_job_name(chat_id: int, slot: str, date_key: str) -> str:
    return f"nag:{chat_id}:{slot}:{date_key}"


def stop_nagging(job_queue, chat_id: int, slot: str, date_key: str) -> int:
    jobs = job_queue.get_jobs_by_name(nag_job_name(chat_id, slot, date_key))
    for j in jobs:
        j.schedule_removal()
    return len(jobs)


# ----------------------------
# JOBS
# ----------------------------
async def send_food_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_cynthia_chat_id(context)
    if not chat_id:
        logger.info("Food reminder skipped: Cynthia not linked yet.")
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="🍽️ Cynthia, time to eat ya! (You need food before/with your medicine.)",
    )


async def send_medicine_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs at 11:00 and 18:00 daily.
    Starts nagging every 30 minutes until Cynthia taps ✅ Taken.
    """
    chat_id = get_cynthia_chat_id(context)
    if not chat_id:
        logger.info("Medicine reminder skipped: Cynthia not linked yet.")
        return

    slot = context.job.data["slot"]  # "morning" or "evening"
    now = datetime.now(TZ)
    date_key = today_key(now)

    # Clear any existing nag for same slot & today (safety)
    existing = context.job_queue.get_jobs_by_name(nag_job_name(chat_id, slot, date_key))
    for j in existing:
        j.schedule_removal()

    label = "🌞 Morning" if slot == "morning" else "🌙 Evening"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Taken", callback_data=f"taken:{slot}:{date_key}")]]
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"💊 {label} medicine time, Cynthia.\n\n"
            "Please take it now (with/after food). Tap ✅ Taken so I stop bugging you."
        ),
        reply_markup=keyboard,
    )

    # Start nag loop
    context.job_queue.run_repeating(
        callback=nag_medicine,
        interval=NAG_EVERY,
        first=now + NAG_EVERY,
        name=nag_job_name(chat_id, slot, date_key),
        data={"slot": slot, "date_key": date_key},
    )


async def nag_medicine(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_cynthia_chat_id(context)
    if not chat_id:
        context.job.schedule_removal()
        return

    slot = context.job.data["slot"]
    date_key = context.job.data["date_key"]
    label = "🌞 Morning" if slot == "morning" else "🌙 Evening"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Taken", callback_data=f"taken:{slot}:{date_key}")]]
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏰ Reminder: {label} medicine not confirmed yet.\n"
            "Please take it now, then tap ✅ Taken."
        ),
        reply_markup=keyboard,
    )


# ----------------------------
# COMMANDS / BUTTONS
# ----------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! I remind Cynthia to eat + take medicine.\n\n"
        "If you are Cynthia, run /iamcynthia.\n"
        "Use /status to see today's nag status."
    )


async def iamcynthia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    set_cynthia_chat_id_runtime(context, chat_id)

    admin_id = get_admin_chat_id()
    msg_for_admin = (
        "✅ Cynthia linked (runtime).\n\n"
        f"Set this in Railway Variables for persistence:\n"
        f"{CYNTHIA_CHAT_ID_ENV}={chat_id}"
    )

    await update.message.reply_text(
        "✅ Linked! I’ll send reminders to you here.\n\n"
        "Daily schedule (Asia/Jakarta):\n"
        "• 10:00 food\n"
        "• 11:00 medicine (nag until ✅ Taken)\n"
        "• 18:00 medicine (nag until ✅ Taken)\n\n"
        "If this bot is running on a server, ask Jessen to set your chat_id in env vars."
    )

    # If you set ADMIN_CHAT_ID, bot will DM you the chat_id automatically.
    if admin_id:
        try:
            await context.bot.send_message(chat_id=admin_id, text=msg_for_admin)
        except Exception as e:
            logger.warning("Could not notify admin: %s", e)
    else:
        logger.info(msg_for_admin)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cynthia_id = get_cynthia_chat_id(context)
    if not cynthia_id:
        await update.message.reply_text(
            "Cynthia not linked yet.\n"
            "Cynthia should run /iamcynthia.\n"
            f"(Recommended for server: set {CYNTHIA_CHAT_ID_ENV} in env vars.)"
        )
        return

    now = datetime.now(TZ)
    dk = today_key(now)

    morning_jobs = context.job_queue.get_jobs_by_name(nag_job_name(cynthia_id, "morning", dk))
    evening_jobs = context.job_queue.get_jobs_by_name(nag_job_name(cynthia_id, "evening", dk))

    env_set = bool(os.getenv(CYNTHIA_CHAT_ID_ENV))

    await update.message.reply_text(
        "📌 Status\n"
        f"• Cynthia chat_id: {cynthia_id}\n"
        f"• Env persistence set: {'YES' if env_set else 'NO'}\n"
        f"• Today (Jakarta): {dk}\n"
        f"• Morning nag active: {'YES' if morning_jobs else 'NO'}\n"
        f"• Evening nag active: {'YES' if evening_jobs else 'NO'}"
    )


async def taken_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    cynthia_id = get_cynthia_chat_id(context)
    if not cynthia_id:
        await query.edit_message_text("Cynthia not linked yet. Run /iamcynthia.")
        return

    # callback_data: taken:{slot}:{date_key}
    try:
        _, slot, date_key = query.data.split(":")
    except ValueError:
        await query.edit_message_text("Button data error.")
        return

    # Only allow Cynthia to confirm in her chat
    if query.message.chat_id != cynthia_id:
        await query.edit_message_text("Only Cynthia can confirm this.")
        return

    removed = stop_nagging(context.job_queue, cynthia_id, slot, date_key)
    label = "Morning" if slot == "morning" else "Evening"

    msg = f"✅ Noted! {label} medicine confirmed. I’ll stop nagging now."
    if removed == 0:
        msg += "\n\n(There wasn’t an active nag timer, but confirmation is recorded.)"

    await query.edit_message_text(msg)


# ----------------------------
# SCHEDULER
# ----------------------------
def schedule_daily_jobs(app: Application) -> None:
    app.job_queue.run_daily(send_food_reminder, time=FOOD_TIME, name="daily_food")

    app.job_queue.run_daily(
        send_medicine_reminder,
        time=MED_MORNING_TIME,
        name="daily_medicine_morning",
        data={"slot": "morning"},
    )

    app.job_queue.run_daily(
        send_medicine_reminder,
        time=MED_EVENING_TIME,
        name="daily_medicine_evening",
        data={"slot": "evening"},
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var.")

    defaults = Defaults(tzinfo=TZ)

    app = Application.builder().token(token).defaults(defaults).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("iamcynthia", iamcynthia_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(taken_button, pattern=r"^taken:"))

    schedule_daily_jobs(app)

    logger.info("Bot running (polling)...")
    app.run_polling(poll_interval=3)


if __name__ == "__main__":
    main()
