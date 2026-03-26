import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.keyboards import (
    channel_keyboard,
    habit_list_keyboard,
    reminder_list_keyboard,
)
from app.config import settings
from app.db import async_session
from app.notifier import add_channel, get_user_channels, remove_channel
from app.scheduler import cancel_job, schedule_habit, schedule_reminder
from app.services import habit_service, reminder_service

logger = logging.getLogger(__name__)

# Conversation states
REMIND_TITLE, REMIND_TIME, REMIND_CRON = range(3)
HABIT_NAME, HABIT_FREQUENCY, HABIT_TIME = range(3, 6)
WHATSAPP_PHONE = 6


# ── /start ──────────────────────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    # Auto-register Telegram notification channel
    async with async_session() as session:
        channels = await get_user_channels(session, user_id)
        has_telegram = any(c.channel_type == "telegram" for c in channels)
        if not has_telegram:
            bot_id, bot_token = settings.telegram_bot_token.split(":", 1)
            apprise_url = f"tgram://{bot_id}/{bot_token}/{user_id}"
            await add_channel(session, user_id, "telegram", apprise_url)

    await update.effective_chat.send_message(
        "Welcome to Reminder App!\n\n"
        "Commands:\n"
        "/remind - Create a reminder\n"
        "/reminders - List active reminders\n"
        "/habit - Create a new habit\n"
        "/habits - Today's habit status\n"
        "/streak - View your streaks\n"
        "/channels - Manage notification channels\n"
        "/help - Show this help"
    )


# ── /help ───────────────────────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await update.effective_chat.send_message(
        "Reminder App Commands:\n\n"
        "/remind - Create a new reminder (guided)\n"
        "/reminders - List & delete reminders\n\n"
        "/habit - Create a new habit (guided)\n"
        "/habits - Today's habits + mark done\n"
        "/streak - View habit streaks\n"
        "/deletehabit - Delete a habit\n\n"
        "/channels - Manage notification channels\n"
        "/help - Show this help"
    )


# ── Reminder conversation ──────────────────────────────────────────────
async def remind_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.effective_chat:
        return ConversationHandler.END
    await update.effective_chat.send_message("What do you want to be reminded about?")
    return REMIND_TITLE


async def remind_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.message or not update.effective_chat or context.user_data is None:
        return ConversationHandler.END
    context.user_data["remind_title"] = update.message.text  # type: ignore[index]
    await update.effective_chat.send_message(
        "When should I remind you?\n\n"
        "For a one-time reminder, send a time like:\n"
        "  30m (30 minutes from now)\n"
        "  2h (2 hours from now)\n"
        "  2025-12-31 14:00\n\n"
        "For recurring, send a cron expression like:\n"
        "  cron 0 9 * * * (every day at 9 AM)\n"
        "  cron 0 9 * * mon-fri (weekdays at 9 AM)",
    )
    return REMIND_TIME


async def remind_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.message or not update.effective_chat or context.user_data is None:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    user_id = str(update.effective_chat.id)
    title = context.user_data.get("remind_title", "Reminder")  # type: ignore[union-attr]

    async with async_session() as session:
        if text.startswith("cron "):
            cron_expr = text[5:].strip()
            reminder = await reminder_service.create_reminder(
                session, user_id, title, cron_expression=cron_expr
            )
            job_id = schedule_reminder(reminder)
            await reminder_service.update_job_id(session, reminder.id, job_id)
            await update.effective_chat.send_message(f"Recurring reminder set: *{title}*\nCron: `{cron_expr}`", parse_mode="Markdown")
        else:
            remind_at = _parse_time(text)
            if remind_at is None:
                await update.effective_chat.send_message("Could not parse that time. Try `30m`, `2h`, or `2025-12-31 14:00`.")
                return REMIND_TIME

            reminder = await reminder_service.create_reminder(
                session, user_id, title, remind_at=remind_at
            )
            job_id = schedule_reminder(reminder)
            await reminder_service.update_job_id(session, reminder.id, job_id)
            await update.effective_chat.send_message(
                f"Reminder set: *{title}*\nAt: {remind_at.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown",
            )

    return ConversationHandler.END


def _parse_time(text: str) -> datetime | None:
    text = text.strip().lower()
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    if text.endswith("m"):
        try:
            minutes = int(text[:-1])
            return now + timedelta(minutes=minutes)
        except ValueError:
            pass
    elif text.endswith("h"):
        try:
            hours = int(text[:-1])
            return now + timedelta(hours=hours)
        except ValueError:
            pass
    else:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.replace(tzinfo=tz)
            except ValueError:
                continue
    return None


# ── /reminders ──────────────────────────────────────────────────────────
async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        reminders = await reminder_service.list_reminders(session, user_id)

    if not reminders:
        await update.effective_chat.send_message("No active reminders.")
        return

    lines = []
    for r in reminders:
        if r.is_recurring:
            lines.append(f"  {r.title} (cron: `{r.cron_expression}`)")
        else:
            time_str = r.remind_at.strftime("%Y-%m-%d %H:%M") if r.remind_at else "?"
            lines.append(f"  {r.title} (at: {time_str})")

    text = "Active reminders:\n" + "\n".join(lines)
    keyboard = reminder_list_keyboard(reminders)
    await update.effective_chat.send_message(text, reply_markup=keyboard, parse_mode="Markdown")


# ── Habit conversation ─────────────────────────────────────────────────
async def habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.effective_chat:
        return ConversationHandler.END
    await update.effective_chat.send_message("What habit do you want to track?")
    return HABIT_NAME


async def habit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.message or not update.effective_chat or context.user_data is None:
        return ConversationHandler.END
    context.user_data["habit_name"] = update.message.text  # type: ignore[index]
    await update.effective_chat.send_message("Frequency? Send `daily` or `weekly` (e.g. `weekly mon,wed,fri`)")
    return HABIT_FREQUENCY


async def habit_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.message or not update.effective_chat or context.user_data is None:
        return ConversationHandler.END
    text = (update.message.text or "").strip().lower()

    if text.startswith("weekly"):
        context.user_data["habit_freq"] = "weekly"  # type: ignore[index]
        parts = text.split(maxsplit=1)
        context.user_data["habit_days"] = parts[1] if len(parts) > 1 else "mon,wed,fri"  # type: ignore[index]
    else:
        context.user_data["habit_freq"] = "daily"  # type: ignore[index]
        context.user_data["habit_days"] = None  # type: ignore[index]

    await update.effective_chat.send_message("What time should I remind you? (e.g. `09:00` or `18:30`)")
    return HABIT_TIME


async def habit_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if not update.message or not update.effective_chat or context.user_data is None:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    user_id = str(update.effective_chat.id)

    try:
        parts = text.split(":")
        reminder_time = time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        await update.effective_chat.send_message("Invalid time format. Use HH:MM (e.g. `09:00`).")
        return HABIT_TIME

    name = context.user_data.get("habit_name", "habit")  # type: ignore[union-attr]
    freq = context.user_data.get("habit_freq", "daily")  # type: ignore[union-attr]
    days = context.user_data.get("habit_days")  # type: ignore[union-attr]

    async with async_session() as session:
        habit = await habit_service.create_habit(
            session,
            user_id,
            name,
            frequency=freq,
            reminder_time=reminder_time,
            reminder_days=days,
        )
        job_id = schedule_habit(
            habit.id, habit.name, user_id,
            reminder_time.hour, reminder_time.minute,
            days=days,
        )
        await habit_service.update_job_id(session, habit.id, job_id)

    await update.effective_chat.send_message(
        f"Habit created: *{name}*\n"
        f"Frequency: {freq}\n"
        f"Reminder at: {reminder_time.strftime('%H:%M')}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /habits ─────────────────────────────────────────────────────────────
async def habits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        status = await habit_service.get_today_status(session, user_id)

    if not status:
        await update.effective_chat.send_message("No habits tracked yet. Use /habit to create one.")
        return

    lines = []
    for habit, done in status:
        mark = "v" if done else "x"
        lines.append(f"  [{mark}] {habit.name}")

    text = "Today's habits:\n" + "\n".join(lines)
    keyboard = habit_list_keyboard(status)
    await update.effective_chat.send_message(text, reply_markup=keyboard)


# ── /streak ─────────────────────────────────────────────────────────────
async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        habits = await habit_service.list_habits(session, user_id)
        if not habits:
            await update.effective_chat.send_message("No habits tracked yet.")
            return

        lines = []
        for habit in habits:
            streak = await habit_service.get_streak(session, habit.id)
            fire = "!" if streak >= 7 else ""
            lines.append(f"  {habit.name}: {streak} day{'s' if streak != 1 else ''} {fire}")

    text = "Habit Streaks:\n" + "\n".join(lines)
    await update.effective_chat.send_message(text)


# ── /deletehabit ────────────────────────────────────────────────────────
async def deletehabit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        habits = await habit_service.list_habits(session, user_id)

    if not habits:
        await update.effective_chat.send_message("No habits to delete.")
        return

    from app.bot.keyboards import habit_delete_keyboard
    keyboard = habit_delete_keyboard(habits)
    await update.effective_chat.send_message("Select a habit to delete:", reply_markup=keyboard)


# ── /channels ───────────────────────────────────────────────────────────
async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        channels = await get_user_channels(session, user_id)

    lines = [f"  {c.channel_type}: active" for c in channels] if channels else ["  No channels configured."]
    text = "Notification channels:\n" + "\n".join(lines)
    keyboard = channel_keyboard()
    await update.effective_chat.send_message(text, reply_markup=keyboard)


# ── Callback query handler ─────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id) if query.from_user else ""

    if data.startswith("del_rem_"):
        reminder_id = int(data[8:])
        async with async_session() as session:
            reminder = await reminder_service.get_reminder(session, reminder_id)
            if reminder and reminder.apscheduler_job_id:
                cancel_job(reminder.apscheduler_job_id)
            await reminder_service.delete_reminder(session, reminder_id)
        await query.edit_message_text("Reminder deleted.")

    elif data.startswith("done_"):
        habit_id = int(data[5:])
        async with async_session() as session:
            completion = await habit_service.mark_complete(session, habit_id)
            if completion:
                streak = await habit_service.get_streak(session, habit_id)
                habit = await habit_service.get_habit(session, habit_id)
                name = habit.name if habit else "habit"
                await query.edit_message_text(f"*{name}* marked done!\nCurrent streak: {streak} day{'s' if streak != 1 else ''}", parse_mode="Markdown")
            else:
                await query.edit_message_text("Already completed today!")

    elif data.startswith("del_hab_"):
        habit_id = int(data[8:])
        async with async_session() as session:
            habit = await habit_service.get_habit(session, habit_id)
            if habit and habit.apscheduler_job_id:
                cancel_job(habit.apscheduler_job_id)
            await habit_service.delete_habit(session, habit_id)
        await query.edit_message_text("Habit deleted.")

    elif data == "add_whatsapp":
        if not update.effective_chat:
            return
        await update.effective_chat.send_message(
            "To add WhatsApp notifications via Twilio:\n\n"
            "Send your WhatsApp phone number with country code:\n"
            "e.g. `+1234567890`\n\n"
            "Make sure you have TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "and TWILIO_FROM_PHONE configured in your .env file.",
            parse_mode="Markdown",
        )
        if context.user_data is not None:
            context.user_data["awaiting_whatsapp"] = True  # type: ignore[index]

    elif data == "list_channels":
        async with async_session() as session:
            channels = await get_user_channels(session, user_id)
        if channels:
            lines = [f"  {i+1}. {c.channel_type}" for i, c in enumerate(channels)]
            await query.edit_message_text("Your channels:\n" + "\n".join(lines))
        else:
            await query.edit_message_text("No channels configured. Use /start to set up Telegram.")


# ── WhatsApp phone number handler ──────────────────────────────────────
async def whatsapp_phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or context.user_data is None:
        return
    if not context.user_data.get("awaiting_whatsapp"):  # type: ignore[union-attr]
        return

    text = (update.message.text or "").strip()
    if not text.startswith("+"):
        await update.effective_chat.send_message("Please send your phone number with country code (e.g. +1234567890)")
        return

    user_id = str(update.effective_chat.id)
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    from_phone = settings.twilio_from_phone

    if not sid or not token or not from_phone:
        await update.effective_chat.send_message(
            "Twilio credentials not configured. Add TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_PHONE to your .env file."
        )
        context.user_data["awaiting_whatsapp"] = False  # type: ignore[index]
        return

    apprise_url = f"twilio://{sid}:{token}@{from_phone}/{text}"

    async with async_session() as session:
        await add_channel(session, user_id, "whatsapp", apprise_url)

    await update.effective_chat.send_message(f"WhatsApp channel added for {text}!")
    context.user_data["awaiting_whatsapp"] = False  # type: ignore[index]


# ── Cancel conversation ────────────────────────────────────────────────
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> object:
    if update.effective_chat:
        await update.effective_chat.send_message("Cancelled.")
    return ConversationHandler.END


# ── Build handlers ──────────────────────────────────────────────────────
def get_handlers() -> list[CommandHandler | ConversationHandler | CallbackQueryHandler | MessageHandler]:  # type: ignore[type-arg]
    remind_conversation = ConversationHandler(  # type: ignore[arg-type]
        entry_points=[CommandHandler("remind", remind_start)],
        states={
            REMIND_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_title)],
            REMIND_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    habit_conversation = ConversationHandler(  # type: ignore[arg-type]
        entry_points=[CommandHandler("habit", habit_start)],
        states={
            HABIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, habit_name)],
            HABIT_FREQUENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, habit_frequency)],
            HABIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, habit_time_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    return [
        CommandHandler("start", start_command),
        CommandHandler("help", help_command),
        remind_conversation,
        habit_conversation,
        CommandHandler("reminders", reminders_command),
        CommandHandler("habits", habits_command),
        CommandHandler("streak", streak_command),
        CommandHandler("deletehabit", deletehabit_command),
        CommandHandler("channels", channels_command),
        CallbackQueryHandler(button_callback),
        MessageHandler(filters.TEXT & ~filters.COMMAND, whatsapp_phone_handler),
    ]
