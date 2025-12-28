import os
import json
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.ext import Defaults

# ----------------------------
# CONFIG
# ----------------------------
TZ = ZoneInfo("Asia/Jakarta")
DATA_FILE = "bot_data.json"

# Times (Asia/Jakarta)
FOOD_TIME = time(hour=10, minute=0, tzinfo=TZ)
MED_MORNING_TIME = time(hour=11, minute=0, tzinfo=TZ)
MED_EVENING_TIME = time(hour=18, minute=0, tzinfo=TZ)

NAG_EVERY = timedelta(minutes=30)  # keep bugging interval

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("med_reminder_bot")


# ----------------------------
# SIMPLE JSON STORAGE
# ----------------------------
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_cynthia_chat_id() -> Optional[int]:
    data = load_data()
    cid = data.get("cynthia_chat_id")
    return int(cid) if cid is not None else None


def set_cynthia_chat_id(chat_id: int) -> None:
    data = load_data()
    data["cynthia_chat_id"] = chat_id
    save_data(data)


# ----------------------------
# HELPERS (JOBS)
# ----------------------------
def nag_job_name(chat_id: int, slot: str, date_key: str) -> str:
    # date_key prevents “yesterday’s nag job” interfering with today
    return f"nag:{chat_id}:{slot}:{date_key}"


def today_key(now: datetime) -> str:
    # YYYY-MM-DD in Jakarta
    return now.astimezone(TZ).strftime("%Y-%m-%d")


async def send_food_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_cynthia_chat_id()
    if not chat_id:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="🍽️ Cynthia, time to eat ya! (You need food before/with your medicine.)",
    )


async def send_medicine_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs at 11:00 and 18:00 daily.
    Creates a nag job every 30 minutes until Cynthia presses the button.
    """
    chat_id = get_cynthia_chat_id()
    if not chat_id:
        return

    slot = context.job.data["slot"]  # "morning" or "evening"
    now = datetime.now(TZ)
    date_key = today_key(now)

    # Cancel any leftover nag jobs for this same slot + today (just in case)
    existing = context.job_queue.get_jobs_by_name(nag_job_name(chat_id, slot, date_key))
    for j in existing:
        j.schedule_removal()

    # Send initial reminder with button
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

    # Schedule nagging every 30 minutes
    context.job_queue.run_repeating(
        callback=nag_medicine,
        interval=NAG_EVERY,
        first=now + NAG_EVERY,
        name=nag_job_name(chat_id, slot, date_key),
        data={"slot": slot, "date_key": date_key},
    )


async def nag_medicine(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = get_cynthia_chat_id()
    if not chat_id:
        # No one linked; stop this job
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


def stop_nagging(job_queue, chat_id: int, slot: str, date_key: str) -> int:
    jobs = job_queue.get_jobs_by_name(nag_job_name(chat_id, slot, date_key))
    for j in jobs:
        j.schedule_removal()
    return len(jobs)


# ----------------------------
# COMMANDS / HANDLERS
# ----------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! I can remind Cynthia to eat + take medicine.\n\n"
        "If you are Cynthia, run /iamcynthia in this chat.\n"
        "Use /status to see if nagging is active today."
    )


async def iamcynthia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    set_cynthia_chat_id(chat_id)
    await update.message.reply_text(
        "✅ Linked! I will send the reminders to you here.\n\n"
        "Daily schedule (Asia/Jakarta):\n"
        "• 10:00 food\n"
        "• 11:00 medicine (nag until ✅ Taken)\n"
        "• 18:00 medicine (nag until ✅ Taken)"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cynthia_id = get_cynthia_chat_id()
    if not cynthia_id:
        await update.message.reply_text("No Cynthia linked yet. Cynthia should run /iamcynthia.")
        return

    now = datetime.now(TZ)
    dk = today_key(now)

    morning_jobs = context.job_queue.get_jobs_by_name(nag_job_name(cynthia_id, "morning", dk))
    evening_jobs = context.job_queue.get_jobs_by_name(nag_job_name(cynthia_id, "evening", dk))

    await update.message.reply_text(
        "📌 Status\n"
        f"• Cynthia linked chat_id: {cynthia_id}\n"
        f"• Today (Jakarta): {dk}\n"
        f"• Morning nag active: {'YES' if morning_jobs else 'NO'}\n"
        f"• Evening nag active: {'YES' if evening_jobs else 'NO'}"
    )


async def taken_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # acknowledge click

    cynthia_id = get_cynthia_chat_id()
    if not cynthia_id:
        await query.edit_message_text("No Cynthia linked yet. Run /iamcynthia.")
        return

    # callback_data: taken:{slot}:{date_key}
    try:
        _, slot, date_key = query.data.split(":")
    except ValueError:
        await query.edit_message_text("Something went wrong with the button data.")
        return

    # Only allow Cynthia to stop nagging (optional safety)
    if query.message.chat_id != cynthia_id:
        await query.edit_message_text("Only Cynthia can confirm this.")
        return

    removed = stop_nagging(context.job_queue, cynthia_id, slot, date_key)

    label = "morning" if slot == "morning" else "evening"
    msg = f"✅ Noted! {label.capitalize()} medicine confirmed. I’ll stop nagging now."
    if removed == 0:
        msg += "\n\n(There wasn’t an active nag timer, but confirmation is recorded anyway.)"

    # Update message to remove button
    await query.edit_message_text(msg)


# ----------------------------
# SCHEDULER SETUP
# ----------------------------
def schedule_daily_jobs(app: Application) -> None:
    # Runs every day at 10:00 Jakarta
    app.job_queue.run_daily(send_food_reminder, time=FOOD_TIME, name="daily_food")

    # Runs every day at 11:00 Jakarta
    app.job_queue.run_daily(
        send_medicine_reminder,
        time=MED_MORNING_TIME,
        name="daily_medicine_morning",
        data={"slot": "morning"},
    )

    # Runs every day at 18:00 Jakarta
    app.job_queue.run_daily(
        send_medicine_reminder,
        time=MED_EVENING_TIME,
        name="daily_medicine_evening",
        data={"slot": "evening"},
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN env var. Example:\n"
            "export TELEGRAM_BOT_TOKEN='123:ABC...'\n"
        )

    defaults = Defaults(tzinfo=TZ)

    app = Application.builder().token(token).defaults(defaults).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("iamcynthia", iamcynthia_command))
    app.add_handler(CommandHandler("status", status_command))

    # Button handler
    app.add_handler(CallbackQueryHandler(taken_button, pattern=r"^taken:"))

    # Schedule jobs
    schedule_daily_jobs(app)

    logger.info("Bot running (polling)...")
    app.run_polling(poll_interval=3)


if __name__ == "__main__":
    main()
