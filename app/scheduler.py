import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import Bot

from app.config import settings
from app.db import async_session
from app.models import Reminder

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.timezone)


async def fire_reminder(reminder_id: int) -> None:
    """Called by APScheduler when a reminder is due."""
    try:
        async with async_session() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None or not reminder.is_active:
                logger.warning("Reminder %s not found or inactive", reminder_id)
                return

            bot = Bot(token=settings.telegram_bot_token)
            async with bot:
                await bot.send_message(
                    chat_id=int(reminder.user_id),
                    text=f"🔔 *Reminder: {reminder.title}*\n{reminder.description or ''}",
                    parse_mode="Markdown",
                )
            logger.info("Sent reminder %s to user %s", reminder.title, reminder.user_id)

            if not reminder.is_recurring:
                reminder.is_active = False
                await session.commit()
    except Exception:
        logger.exception("Failed to fire reminder %s", reminder_id)


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

    # Remove existing job if any
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
        trigger = DateTrigger(run_date=reminder.remind_at)
        scheduler.add_job(
            fire_reminder,
            trigger=trigger,
            args=[reminder.id],
            id=job_id,
            replace_existing=True,
        )

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
