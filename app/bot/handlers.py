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
from app.scheduler import cancel_job, dismiss_reminder, schedule_habit, schedule_reminder, snooze_reminder
from app.services import habit_service, reminder_service
from app.services.shame_service import VALID_LEVELS, add_custom_shame, delete_custom_shame, list_custom_shames, toggle_shame

logger = logging.getLogger(__name__)

# Conversation states
REMIND_TITLE, REMIND_TIME, REMIND_CRON = range(3)
HABIT_NAME, HABIT_FREQUENCY, HABIT_TIME = range(3, 6)
WHATSAPP_PHONE = 6
EDIT_HABIT_TIME = 7


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
        "/today - Your day at a glance\n"
        "/ask - Smart reminder (natural language)\n"
        "/remind - Create a new reminder (guided)\n"
        "/reminders - List & delete reminders\n\n"
        "/habit - Create a new habit (guided)\n"
        "/habits - Today's habits + mark done\n"
        "/streak - View habit streaks\n"
        "/stats - Habit analytics + completion rates\n"
        "/edithabit - Change habit reminder time\n"
        "/shame - Toggle shame mode per habit\n"
        "/addshame - Add custom shame message\n"
        "/myshames - List your custom messages\n"
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


# ── /stats ──────────────────────────────────────────────────────────────
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        stats = await habit_service.get_habit_stats(session, user_id)

    if not stats:
        await update.effective_chat.send_message("No habits tracked yet. Use /habit to create one.")
        return

    lines = ["📊 *Habit Stats*\n"]
    for s in stats:
        pct = round(s.week_done / s.week_total * 100) if s.week_total > 0 else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"*{s.name}*")
        lines.append(f"  Week:  {bar} {s.week_done}/{s.week_total} ({pct}%)")
        lines.append(f"  Month: {s.month_done}/{s.month_total}")
        lines.append(f"  Streak: {s.current_streak} days (best: {s.best_streak})")
        lines.append("")

    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown")


# ── /today ──────────────────────────────────────────────────────────────
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    lines = [f"📅 *Today — {now.strftime('%A, %B %d')}*\n"]

    # Habits
    async with async_session() as session:
        status = await habit_service.get_today_status(session, user_id)
        reminders = await reminder_service.list_reminders(session, user_id)

    if status:
        done_count = sum(1 for _, done in status if done)
        lines.append(f"*Habits* ({done_count}/{len(status)})")
        for habit, done in status:
            mark = "✅" if done else "⬜"
            lines.append(f"  {mark} {habit.name}")
        lines.append("")

    # Reminders
    today_reminders = []
    for r in reminders:
        if r.remind_at and r.remind_at.date() == now.date():
            today_reminders.append(r)
        elif r.is_recurring:
            today_reminders.append(r)

    if today_reminders:
        lines.append("*Reminders*")
        for r in today_reminders:
            if r.is_recurring:
                lines.append(f"  🔁 {r.title}")
            else:
                time_str = r.remind_at.strftime("%H:%M") if r.remind_at else ""
                lines.append(f"  🔔 {r.title} at {time_str}")
        lines.append("")

    if not status and not today_reminders:
        lines.append("Nothing scheduled for today.")

    keyboard = habit_list_keyboard(status) if status else None
    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


# ── /shame ──────────────────────────────────────────────────────────────
async def shame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        habits = await habit_service.list_habits(session, user_id)

    if not habits:
        await update.effective_chat.send_message("No habits yet. Use /habit to create one.")
        return

    from app.bot.keyboards import shame_toggle_keyboard
    lines = ["😈 *Shame Mode*\n", "Toggle shaming per habit:\n"]
    for h in habits:
        status = "ON" if h.shame_enabled else "OFF"
        lines.append(f"  {h.name}: {status}")

    keyboard = shame_toggle_keyboard(habits)
    await update.effective_chat.send_message("\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")


# ── /addshame — add custom shame message ────────────────────────────────
async def addshame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    text = (update.message.text or "").replace("/addshame", "", 1).strip()
    if not text:
        levels = ", ".join(VALID_LEVELS)
        await update.effective_chat.send_message(
            "Add a custom shame message:\n\n"
            f"/addshame <level> <message>\n\n"
            f"Levels: {levels}\n\n"
            "Use {name} for the habit name and {days} for missed days.\n\n"
            "Examples:\n"
            "  /addshame gentle Hey, '{name}' is lonely without you\n"
            "  /addshame nuclear {days} days! '{name}' has left the chat permanently"
        )
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_chat.send_message("Usage: /addshame <level> <message>")
        return

    level = parts[0].lower()
    message = parts[1]

    if level not in VALID_LEVELS:
        levels = ", ".join(VALID_LEVELS)
        await update.effective_chat.send_message(f"Invalid level. Choose from: {levels}")
        return

    user_id = str(update.effective_chat.id)
    async with async_session() as session:
        await add_custom_shame(session, user_id, level, message)

    await update.effective_chat.send_message(f"Added custom *{level}* shame message!", parse_mode="Markdown")


# ── /myshames — list custom shame messages ──────────────────────────────
async def myshames_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        messages = await list_custom_shames(session, user_id)

    if not messages:
        await update.effective_chat.send_message("No custom shame messages yet. Use /addshame to add one.")
        return

    lines = ["Your custom shame messages:\n"]
    for msg in messages:
        lines.append(f"  [{msg.level}] {msg.message}")
        lines.append(f"  Delete: /delshame {msg.id}")
        lines.append("")

    await update.effective_chat.send_message("\n".join(lines))


# ── /delshame — delete custom shame message ─────────────────────────────
async def delshame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    text = (update.message.text or "").replace("/delshame", "", 1).strip()
    if not text:
        await update.effective_chat.send_message("Usage: /delshame <id>\nUse /myshames to see your messages and their IDs.")
        return

    try:
        message_id = int(text)
    except ValueError:
        await update.effective_chat.send_message("Invalid ID. Use /myshames to see your messages.")
        return

    async with async_session() as session:
        deleted = await delete_custom_shame(session, message_id)

    if deleted:
        await update.effective_chat.send_message("Shame message deleted.")
    else:
        await update.effective_chat.send_message("Message not found.")


# ── /edithabit ──────────────────────────────────────────────────────────
async def edithabit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    user_id = str(update.effective_chat.id)

    async with async_session() as session:
        habits = await habit_service.list_habits(session, user_id)

    if not habits:
        await update.effective_chat.send_message("No habits yet.")
        return

    from app.bot.keyboards import habit_edit_keyboard
    keyboard = habit_edit_keyboard(habits)
    await update.effective_chat.send_message("Select a habit to edit its reminder time:", reply_markup=keyboard)


# ── /testshame — generate a shame message for testing ───────────────────
async def testshame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    text = (update.message.text or "").replace("/testshame", "", 1).strip()
    if not text:
        await update.effective_chat.send_message(
            "Usage: /testshame <habit_name> <missed_days>\n\n"
            "Example: /testshame Exercise 5"
        )
        return

    parts = text.rsplit(maxsplit=1)
    habit_name = parts[0]
    try:
        missed_days = int(parts[1]) if len(parts) > 1 else 3
    except ValueError:
        missed_days = 3

    from app.services.shame_service import get_shame_level, get_shame_message
    level = get_shame_level(missed_days)

    user_id = str(update.effective_chat.id)
    async with async_session() as session:
        message = await get_shame_message(session, user_id, habit_name, missed_days)

    await update.effective_chat.send_message(
        f"😈 [{level}, {missed_days} days missed]\n\n{message}"
    )


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
    logger.info("Callback received: data=%s, user=%s", data, user_id)

    if data.startswith("noop_"):
        await query.edit_message_text("Already completed today! 💪")
        return

    if data.startswith("dismiss_"):
        reminder_id = int(data[8:])
        dismiss_reminder(reminder_id)
        async with async_session() as session:
            await reminder_service.deactivate_reminder(session, reminder_id)
        await query.edit_message_text("Reminder dismissed.")

    elif data.startswith("snooze_"):
        # Format: snooze_5_123 or snooze_15_123
        parts = data.split("_")
        minutes = int(parts[1])
        reminder_id = int(parts[2])
        snooze_reminder(reminder_id, minutes)
        await query.edit_message_text(f"Snoozed for {minutes} minutes.")

    elif data.startswith("del_rem_"):
        reminder_id = int(data[8:])
        async with async_session() as session:
            reminder = await reminder_service.get_reminder(session, reminder_id)
            if reminder and reminder.apscheduler_job_id:
                cancel_job(reminder.apscheduler_job_id)
            await reminder_service.delete_reminder(session, reminder_id)
        await query.edit_message_text("Reminder deleted.")

    elif data.startswith("done_"):
        habit_id = int(data[5:])
        logger.info("Done button pressed for habit_id=%s", habit_id)
        async with async_session() as session:
            completion = await habit_service.mark_complete(session, habit_id)
            logger.info("mark_complete result: %s", completion)
            if completion:
                streak = await habit_service.get_streak(session, habit_id)
                habit = await habit_service.get_habit(session, habit_id)
                name = habit.name if habit else "habit"
                base_msg = f"{name} marked done!\nCurrent streak: {streak} day{'s' if streak != 1 else ''}"
                # Generate praise (non-blocking — don't let it break the flow)
                try:
                    from app.services.llm_service import generate_praise
                    praise = await generate_praise(name, streak)
                    await query.edit_message_text(f"{base_msg}\n\n🎉 {praise}")
                except Exception:
                    logger.exception("Praise generation failed")
                    await query.edit_message_text(base_msg)
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

    elif data.startswith("shame_"):
        habit_id = int(data[6:])
        async with async_session() as session:
            new_state = await toggle_shame(session, habit_id)
            if new_state is not None:
                habit = await habit_service.get_habit(session, habit_id)
                name = habit.name if habit else "habit"
                emoji = "😈" if new_state else "😇"
                state_text = "enabled" if new_state else "disabled"
                await query.edit_message_text(f"{emoji} Shame mode *{state_text}* for *{name}*", parse_mode="Markdown")
            else:
                await query.edit_message_text("Habit not found.")

    elif data.startswith("edit_hab_"):
        habit_id = int(data[9:])
        if context.user_data is not None:
            context.user_data["editing_habit_id"] = habit_id  # type: ignore[index]
        await query.edit_message_text(f"Send the new reminder time (e.g. `09:00` or `18:30`):")

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


# ── Free text handler (edit habit time, whatsapp phone) ─────────────────
async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or context.user_data is None:
        return

    text = (update.message.text or "").strip()

    # Handle edit habit time
    editing_id = context.user_data.get("editing_habit_id")  # type: ignore[union-attr]
    if editing_id is not None:
        try:
            parts = text.split(":")
            new_time = time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            await update.effective_chat.send_message("Invalid time format. Use HH:MM (e.g. `09:00`).")
            return

        user_id = str(update.effective_chat.id)
        async with async_session() as session:
            habit = await habit_service.get_habit(session, editing_id)
            if habit:
                habit.reminder_time = new_time
                await session.commit()
                # Reschedule
                if habit.apscheduler_job_id:
                    cancel_job(habit.apscheduler_job_id)
                job_id = schedule_habit(
                    habit.id, habit.name, user_id,
                    new_time.hour, new_time.minute,
                    days=habit.reminder_days,
                )
                await habit_service.update_job_id(session, habit.id, job_id)
                await update.effective_chat.send_message(
                    f"Updated *{habit.name}* reminder to {new_time.strftime('%H:%M')}",
                    parse_mode="Markdown",
                )
            else:
                await update.effective_chat.send_message("Habit not found.")

        context.user_data["editing_habit_id"] = None  # type: ignore[index]
        return

    # Handle WhatsApp phone number
    if context.user_data.get("awaiting_whatsapp"):  # type: ignore[union-attr]
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
        return

    # Smart fallback: try LLM to understand the message
    from app.services.llm_service import classify_intent, is_available, parse_natural_language

    if not await is_available():
        return  # No LLM, ignore unrecognized text

    user_id = str(update.effective_chat.id)

    # Get user's habit names for context
    async with async_session() as session:
        habits = await habit_service.list_habits(session, user_id)
    habit_names = [h.name for h in habits]

    classified = await classify_intent(text, habit_names)
    if not classified:
        return

    intent = classified.get("intent")

    if intent == "reminder":
        # Parse as a reminder
        parsed = await parse_natural_language(text)
        if not parsed:
            await update.effective_chat.send_message("I think you want a reminder but couldn't parse it. Try /remind for the guided flow.")
            return

        title = str(parsed.get("title", text))
        time_type = parsed.get("time_type")
        tz = ZoneInfo(settings.timezone)

        async with async_session() as session:
            if time_type == "relative":
                minutes = int(parsed.get("relative_minutes") or 5)
                remind_at = datetime.now(tz) + timedelta(minutes=minutes)
                reminder = await reminder_service.create_reminder(session, user_id, title, remind_at=remind_at)
                job_id = schedule_reminder(reminder)
                await reminder_service.update_job_id(session, reminder.id, job_id)
                await update.effective_chat.send_message(
                    f"Reminder set: *{title}*\nAt: {remind_at.strftime('%Y-%m-%d %H:%M')}",
                    parse_mode="Markdown",
                )
            elif time_type == "absolute":
                abs_time = str(parsed.get("absolute_time", ""))
                try:
                    remind_at = datetime.strptime(abs_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                except ValueError:
                    await update.effective_chat.send_message(f"Could not parse time: {abs_time}")
                    return
                reminder = await reminder_service.create_reminder(session, user_id, title, remind_at=remind_at)
                job_id = schedule_reminder(reminder)
                await reminder_service.update_job_id(session, reminder.id, job_id)
                await update.effective_chat.send_message(
                    f"Reminder set: *{title}*\nAt: {remind_at.strftime('%Y-%m-%d %H:%M')}",
                    parse_mode="Markdown",
                )
            elif time_type == "cron":
                cron_expr = str(parsed.get("cron_expression", ""))
                reminder = await reminder_service.create_reminder(session, user_id, title, cron_expression=cron_expr)
                job_id = schedule_reminder(reminder)
                await reminder_service.update_job_id(session, reminder.id, job_id)
                await update.effective_chat.send_message(
                    f"Recurring reminder set: *{title}*\nSchedule: `{cron_expr}`",
                    parse_mode="Markdown",
                )

    elif intent == "habit_done":
        habit_name = classified.get("habit_name")
        if habit_name:
            async with async_session() as session:
                for h in habits:
                    if h.name.lower() == habit_name.lower():
                        completion = await habit_service.mark_complete(session, h.id)
                        if completion:
                            streak = await habit_service.get_streak(session, h.id)
                            base_msg = f"{h.name} marked done!\nCurrent streak: {streak} day{'s' if streak != 1 else ''}"
                            try:
                                from app.services.llm_service import generate_praise
                                praise = await generate_praise(h.name, streak)
                                await update.effective_chat.send_message(f"{base_msg}\n\n🎉 {praise}")
                            except Exception:
                                await update.effective_chat.send_message(base_msg)
                        else:
                            await update.effective_chat.send_message(f"{h.name} already done today!")
                        return
            await update.effective_chat.send_message("Couldn't find that habit. Use /habits to see your list.")

    elif intent in ("question", "chat"):
        response = classified.get("response")
        if response:
            await update.effective_chat.send_message(response)


# ── /ask — natural language reminders via LLM ───────────────────────────
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    from app.services.llm_service import is_available, parse_natural_language

    if not await is_available():
        await update.effective_chat.send_message("LLM is not available. Use /remind for the guided flow.")
        return

    text = (update.message.text or "").replace("/ask", "", 1).strip()
    if not text:
        await update.effective_chat.send_message(
            "Tell me what to remind you about:\n\n"
            "Examples:\n"
            "  /ask call mom in 30 minutes\n"
            "  /ask take medicine every day at 9am\n"
            "  /ask meeting tomorrow at 2pm\n"
            "  /ask water plants every monday at 8am"
        )
        return

    await update.effective_chat.send_message("Thinking...")

    parsed = await parse_natural_language(text)
    if not parsed:
        await update.effective_chat.send_message("Couldn't understand that. Try /remind for the guided flow.")
        return

    user_id = str(update.effective_chat.id)
    title = str(parsed.get("title", text))
    time_type = parsed.get("time_type")
    tz = ZoneInfo(settings.timezone)

    async with async_session() as session:
        if time_type == "relative":
            minutes = int(parsed.get("relative_minutes") or 5)
            remind_at = datetime.now(tz) + timedelta(minutes=minutes)
            reminder = await reminder_service.create_reminder(session, user_id, title, remind_at=remind_at)
            job_id = schedule_reminder(reminder)
            await reminder_service.update_job_id(session, reminder.id, job_id)
            await update.effective_chat.send_message(
                f"Reminder set: *{title}*\nAt: {remind_at.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown",
            )

        elif time_type == "absolute":
            abs_time = str(parsed.get("absolute_time", ""))
            try:
                remind_at = datetime.strptime(abs_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                await update.effective_chat.send_message(f"Could not parse time: {abs_time}")
                return
            reminder = await reminder_service.create_reminder(session, user_id, title, remind_at=remind_at)
            job_id = schedule_reminder(reminder)
            await reminder_service.update_job_id(session, reminder.id, job_id)
            await update.effective_chat.send_message(
                f"Reminder set: *{title}*\nAt: {remind_at.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown",
            )

        elif time_type == "cron":
            cron_expr = str(parsed.get("cron_expression", ""))
            reminder = await reminder_service.create_reminder(session, user_id, title, cron_expression=cron_expr)
            job_id = schedule_reminder(reminder)
            await reminder_service.update_job_id(session, reminder.id, job_id)
            await update.effective_chat.send_message(
                f"Recurring reminder set: *{title}*\nSchedule: `{cron_expr}`",
                parse_mode="Markdown",
            )

        else:
            await update.effective_chat.send_message("Couldn't figure out when to remind you. Try /remind for the guided flow.")


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
        CommandHandler("edithabit", edithabit_command),
        CommandHandler("testshame", testshame_command),
        CommandHandler("deletehabit", deletehabit_command),
        CommandHandler("ask", ask_command),
        CommandHandler("channels", channels_command),
        CommandHandler("stats", stats_command),
        CommandHandler("shame", shame_command),
        CommandHandler("addshame", addshame_command),
        CommandHandler("myshames", myshames_command),
        CommandHandler("delshame", delshame_command),
        CommandHandler("today", today_command),
        CallbackQueryHandler(button_callback),
        MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler),
    ]
