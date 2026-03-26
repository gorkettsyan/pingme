import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.db import async_session
from app.models import Reminder

logger = logging.getLogger(__name__)

MAX_SNOOZE_RETRIES = 3

scheduler = AsyncIOScheduler(
    timezone=settings.timezone,
    job_defaults={"misfire_grace_time": 300, "coalesce": True},
)


async def fire_reminder(reminder_id: int, attempt: int = 1) -> None:
    """Called by APScheduler when a reminder is due."""
    logger.info("fire_reminder called for reminder_id=%s (attempt %s)", reminder_id, attempt)
    try:
        async with async_session() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None or not reminder.is_active:
                logger.warning("Reminder %s not found or inactive", reminder_id)
                return

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Dismiss", callback_data=f"dismiss_{reminder.id}"),
                    InlineKeyboardButton("Snooze 5m", callback_data=f"snooze_5_{reminder.id}"),
                    InlineKeyboardButton("Snooze 15m", callback_data=f"snooze_15_{reminder.id}"),
                ],
            ])

            attempt_text = f" (repeat {attempt}/{MAX_SNOOZE_RETRIES})" if attempt > 1 else ""
            bot = Bot(token=settings.telegram_bot_token)
            async with bot:
                await bot.send_message(
                    chat_id=int(reminder.user_id),
                    text=f"🔔 *Reminder: {reminder.title}*{attempt_text}\n{reminder.description or ''}",
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            logger.info("Sent reminder %s to user %s (attempt %s)", reminder.title, reminder.user_id, attempt)

            # Schedule auto-repeat if not dismissed (for one-time reminders)
            if not reminder.is_recurring and attempt < MAX_SNOOZE_RETRIES:
                tz = ZoneInfo(settings.timezone)
                repeat_at = datetime.now(tz) + timedelta(minutes=5)
                repeat_job_id = f"reminder_{reminder.id}_repeat_{attempt + 1}"
                scheduler.add_job(
                    fire_reminder,
                    trigger=DateTrigger(run_date=repeat_at, timezone=settings.timezone),
                    args=[reminder.id, attempt + 1],
                    id=repeat_job_id,
                    replace_existing=True,
                )
                logger.info("Scheduled auto-repeat %s at %s", repeat_job_id, repeat_at)

    except Exception:
        logger.exception("Failed to fire reminder %s", reminder_id)


def dismiss_reminder(reminder_id: int) -> None:
    """Cancel all pending repeat jobs for a reminder."""
    for i in range(2, MAX_SNOOZE_RETRIES + 1):
        job_id = f"reminder_{reminder_id}_repeat_{i}"
        job = scheduler.get_job(job_id)
        if job:
            job.remove()
            logger.info("Cancelled repeat job %s", job_id)


def snooze_reminder(reminder_id: int, minutes: int) -> None:
    """Cancel pending repeats and reschedule after N minutes."""
    dismiss_reminder(reminder_id)
    tz = ZoneInfo(settings.timezone)
    snooze_at = datetime.now(tz) + timedelta(minutes=minutes)
    job_id = f"reminder_{reminder_id}_snooze"
    scheduler.add_job(
        fire_reminder,
        trigger=DateTrigger(run_date=snooze_at, timezone=settings.timezone),
        args=[reminder_id, 1],
        id=job_id,
        replace_existing=True,
    )
    logger.info("Snoozed reminder %s for %s minutes", reminder_id, minutes)


async def fire_habit_checkin(habit_id: int, habit_name: str, user_id: str) -> None:
    """Called by APScheduler to send habit check-in reminders."""
    try:
        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            await bot.send_message(
                chat_id=int(user_id),
                text=f"📋 *Habit Check-in: {habit_name}*\nTime to work on it! Use /habits to mark it done.",
                parse_mode="Markdown",
            )
        logger.info("Sent habit checkin for %s to user %s", habit_name, user_id)
    except Exception:
        logger.exception("Failed to send habit checkin for %s", habit_name)


def schedule_reminder(reminder: Reminder) -> str:
    job_id = f"reminder_{reminder.id}"

    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    if reminder.is_recurring and reminder.cron_expression:
        trigger = CronTrigger.from_crontab(reminder.cron_expression)
        scheduler.add_job(
            fire_reminder,
            trigger=trigger,
            args=[reminder.id],
            id=job_id,
            replace_existing=True,
        )
    elif reminder.remind_at:
        trigger = DateTrigger(run_date=reminder.remind_at, timezone=settings.timezone)
        job = scheduler.add_job(
            fire_reminder,
            trigger=trigger,
            args=[reminder.id],
            id=job_id,
            replace_existing=True,
        )
        logger.info("Scheduled one-time reminder %s, next_run=%s, trigger=%s", reminder.id, job.next_run_time, trigger)

    return job_id


def schedule_habit(habit_id: int, habit_name: str, user_id: str, hour: int, minute: int, days: str | None = None) -> str:
    job_id = f"habit_{habit_id}"

    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    if days:
        trigger = CronTrigger(day_of_week=days, hour=hour, minute=minute)
    else:
        trigger = CronTrigger(hour=hour, minute=minute)

    scheduler.add_job(
        fire_habit_checkin,
        trigger=trigger,
        args=[habit_id, habit_name, user_id],
        id=job_id,
        replace_existing=True,
    )
    return job_id


def cancel_job(job_id: str) -> bool:
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
        return True
    return False
