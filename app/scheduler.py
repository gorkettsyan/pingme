import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from sqlalchemy import select

from app.db import async_session
from app.models import Goal, Habit, Reminder

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


async def fire_goal_checkin(goal_id: int, goal_name: str, user_id: str) -> None:
    """Called by APScheduler to send goal check-in reminders."""
    try:
        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            await bot.send_message(
                chat_id=int(user_id),
                text=f"🎯 *Goal Check-in: {goal_name}*\nTime to make progress! Use /goals to log it.",
                parse_mode="Markdown",
            )
        logger.info("Sent goal checkin for %s to user %s", goal_name, user_id)
    except Exception:
        logger.exception("Failed to send goal checkin for %s", goal_name)


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


async def send_shame_check() -> None:
    """Check all habits with shame enabled and roast users who didn't complete them."""
    logger.info("Running shame check")
    try:
        from app.services.shame_service import get_shame_message, get_shameable_habits

        async with async_session() as session:
            # Get all unique user IDs with active shame habits
            result = await session.execute(
                select(Habit.user_id)
                .where(Habit.is_active == True, Habit.shame_enabled == True)  # noqa: E712
                .distinct()
            )
            user_ids = [row[0] for row in result.all()]

        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            for user_id in user_ids:
                try:
                    async with async_session() as session:
                        shameable = await get_shameable_habits(session, user_id)

                        for habit, missed_days in shameable:
                            message = await get_shame_message(session, user_id, habit.name, missed_days)
                            await bot.send_message(
                                chat_id=int(user_id),
                                text=f"😈 {message}",
                            )
                            logger.info("Shamed user %s for habit %s (%s days)", user_id, habit.name, missed_days)
                except Exception:
                    logger.exception("Failed to shame user %s", user_id)
    except Exception:
        logger.exception("Failed to run shame check")


async def send_goal_morning_summary() -> None:
    """Send a morning summary of active goals to all users."""
    logger.info("Sending goal morning summaries")
    try:
        from app.services.goal_service import get_today_status, list_goals

        async with async_session() as session:
            result = await session.execute(
                select(Goal.user_id).where(Goal.is_active == True).distinct()  # noqa: E712
            )
            user_ids = [row[0] for row in result.all()]

        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            for user_id in user_ids:
                try:
                    async with async_session() as session:
                        status = await get_today_status(session, user_id)
                    if not status:
                        continue

                    lines = ["🎯 *Good morning! Here are your goals for today:*\n"]
                    for goal, today_count, total in status:
                        quota_mark = f"{today_count}/{goal.daily_quota}"
                        lines.append(f"  • *{goal.name}*: {quota_mark} {goal.unit}")
                        if goal.target_count:
                            pct = round(total / goal.target_count * 100) if goal.target_count > 0 else 0
                            lines.append(f"    Progress: {total}/{goal.target_count} ({pct}%)")
                        if goal.deadline:
                            from datetime import date
                            days_left = (goal.deadline - date.today()).days
                            lines.append(f"    Deadline: {days_left} days left")

                    lines.append("\nUse /goals to log progress!")
                    await bot.send_message(
                        chat_id=int(user_id),
                        text="\n".join(lines),
                        parse_mode="Markdown",
                    )
                    logger.info("Sent goal morning summary to user %s", user_id)
                except Exception:
                    logger.exception("Failed to send goal morning summary to user %s", user_id)
    except Exception:
        logger.exception("Failed to send goal morning summaries")


def schedule_goal_morning_summary() -> None:
    """Schedule the goal morning summary for every day at 08:00."""
    scheduler.add_job(
        send_goal_morning_summary,
        trigger=CronTrigger(hour=8, minute=0),
        id="goal_morning_summary",
        replace_existing=True,
    )
    logger.info("Goal morning summary scheduled for every day at 08:00")


def schedule_shame_check() -> None:
    """Schedule the shame check for every day at 21:00."""
    scheduler.add_job(
        send_shame_check,
        trigger=CronTrigger(hour=21, minute=0),
        id="shame_check",
        replace_existing=True,
    )
    logger.info("Shame check scheduled for every day at 21:00")


async def send_weekly_summary() -> None:
    """Send weekly summary to all users with habits. Runs every Sunday at 20:00."""
    logger.info("Sending weekly summaries")
    try:
        from app.services.habit_service import get_weekly_summary

        async with async_session() as session:
            # Get all unique user IDs that have active habits
            result = await session.execute(
                select(Habit.user_id).where(Habit.is_active == True).distinct()  # noqa: E712
            )
            user_ids = [row[0] for row in result.all()]

        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            for user_id in user_ids:
                try:
                    async with async_session() as session:
                        summary = await get_weekly_summary(session, user_id)
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=summary,
                        parse_mode="Markdown",
                    )
                    logger.info("Sent weekly summary to user %s", user_id)
                except Exception:
                    logger.exception("Failed to send weekly summary to user %s", user_id)
    except Exception:
        logger.exception("Failed to send weekly summaries")


def schedule_weekly_summary() -> None:
    """Schedule the weekly summary for every Sunday at 20:00."""
    scheduler.add_job(
        send_weekly_summary,
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_summary",
        replace_existing=True,
    )
    logger.info("Weekly summary scheduled for every Sunday at 20:00")


async def restore_jobs() -> None:
    """Restore all scheduled jobs from the database after a restart."""
    async with async_session() as session:
        # Restore recurring reminders
        result = await session.execute(
            select(Reminder).where(
                Reminder.is_active == True,  # noqa: E712
                Reminder.is_recurring == True,  # noqa: E712
            )
        )
        for (reminder,) in result.all():
            schedule_reminder(reminder)
            logger.info("Restored recurring reminder: %s", reminder.title)

        # Restore one-time reminders that haven't fired yet
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
        result = await session.execute(
            select(Reminder).where(
                Reminder.is_active == True,  # noqa: E712
                Reminder.is_recurring == False,  # noqa: E712
                Reminder.remind_at > now,
            )
        )
        for (reminder,) in result.all():
            schedule_reminder(reminder)
            logger.info("Restored one-time reminder: %s at %s", reminder.title, reminder.remind_at)

        # Restore habit reminders
        result = await session.execute(
            select(Habit).where(Habit.is_active == True)  # noqa: E712
        )
        for (habit,) in result.all():
            if habit.reminder_time:
                schedule_habit(
                    habit.id, habit.name, habit.user_id,
                    habit.reminder_time.hour, habit.reminder_time.minute,
                    days=habit.reminder_days,
                )
                logger.info("Restored habit reminder: %s at %s", habit.name, habit.reminder_time)

        # Restore goal reminders
        result = await session.execute(
            select(Goal).where(Goal.is_active == True)  # noqa: E712
        )
        for (goal,) in result.all():
            if goal.reminder_time:
                schedule_goal(
                    goal.id, goal.name, goal.user_id,
                    goal.reminder_time.hour, goal.reminder_time.minute,
                )
                logger.info("Restored goal reminder: %s at %s", goal.name, goal.reminder_time)

    logger.info("All jobs restored from database")


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
        trigger = CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=settings.timezone)
    else:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=settings.timezone)

    job = scheduler.add_job(
        fire_habit_checkin,
        trigger=trigger,
        args=[habit_id, habit_name, user_id],
        id=job_id,
        replace_existing=True,
    )
    logger.info("Scheduled habit %s (%s), next_run=%s", habit_name, job_id, job.next_run_time)
    return job_id


def schedule_goal(goal_id: int, goal_name: str, user_id: str, hour: int, minute: int) -> str:
    job_id = f"goal_{goal_id}"

    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    trigger = CronTrigger(hour=hour, minute=minute, timezone=settings.timezone)
    job = scheduler.add_job(
        fire_goal_checkin,
        trigger=trigger,
        args=[goal_id, goal_name, user_id],
        id=job_id,
        replace_existing=True,
    )
    logger.info("Scheduled goal %s (%s), next_run=%s", goal_name, job_id, job.next_run_time)
    return job_id


def cancel_job(job_id: str) -> bool:
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
        return True
    return False
