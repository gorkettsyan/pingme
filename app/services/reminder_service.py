from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reminder


async def create_reminder(
    session: AsyncSession,
    user_id: str,
    title: str,
    remind_at: datetime | None = None,
    cron_expression: str | None = None,
    description: str | None = None,
) -> Reminder:
    is_recurring = cron_expression is not None
    reminder = Reminder(
        user_id=user_id,
        title=title,
        description=description,
        remind_at=remind_at,
        cron_expression=cron_expression,
        is_recurring=is_recurring,
        is_active=True,
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def list_reminders(session: AsyncSession, user_id: str) -> list[Reminder]:
    result = await session.execute(
        select(Reminder).where(Reminder.user_id == user_id, Reminder.is_active == True).order_by(Reminder.created_at)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_reminder(session: AsyncSession, reminder_id: int) -> Reminder | None:
    return await session.get(Reminder, reminder_id)


async def deactivate_reminder(session: AsyncSession, reminder_id: int) -> bool:
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        return False
    reminder.is_active = False
    await session.commit()
    return True


async def delete_reminder(session: AsyncSession, reminder_id: int) -> bool:
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        return False
    await session.delete(reminder)
    await session.commit()
    return True


async def update_job_id(session: AsyncSession, reminder_id: int, job_id: str) -> None:
    reminder = await session.get(Reminder, reminder_id)
    if reminder:
        reminder.apscheduler_job_id = job_id
        await session.commit()
