from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Habit, HabitCompletion


async def create_habit(
    session: AsyncSession,
    user_id: str,
    name: str,
    frequency: str = "daily",
    reminder_time: time | None = None,
    reminder_days: str | None = None,
    description: str | None = None,
) -> Habit:
    habit = Habit(
        user_id=user_id,
        name=name,
        description=description,
        frequency=frequency,
        reminder_time=reminder_time,
        reminder_days=reminder_days,
        is_active=True,
    )
    session.add(habit)
    await session.commit()
    await session.refresh(habit)
    return habit


async def list_habits(session: AsyncSession, user_id: str) -> list[Habit]:
    result = await session.execute(
        select(Habit).where(Habit.user_id == user_id, Habit.is_active == True).order_by(Habit.created_at)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_habit(session: AsyncSession, habit_id: int) -> Habit | None:
    return await session.get(Habit, habit_id)


async def mark_complete(
    session: AsyncSession,
    habit_id: int,
    for_date: date | None = None,
) -> HabitCompletion | None:
    target_date = for_date or date.today()

    # Check if already completed today
    existing = await session.execute(
        select(HabitCompletion).where(
            HabitCompletion.habit_id == habit_id,
            HabitCompletion.date == target_date,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None  # Already completed

    completion = HabitCompletion(
        habit_id=habit_id,
        date=target_date,
        completed_at=datetime.now(),
    )
    session.add(completion)
    await session.commit()
    await session.refresh(completion)
    return completion


async def get_streak(session: AsyncSession, habit_id: int) -> int:
    """Calculate current streak of consecutive days with completions."""
    result = await session.execute(
        select(HabitCompletion.date)
        .where(HabitCompletion.habit_id == habit_id)
        .order_by(HabitCompletion.date.desc())
    )
    dates = [row[0] for row in result.all()]

    if not dates:
        return 0

    streak = 0
    # Start from today or yesterday (allow today to still be in progress)
    check_date = date.today()
    if dates[0] != check_date:
        check_date = check_date - timedelta(days=1)
        if dates[0] != check_date:
            return 0  # Streak already broken

    for d in dates:
        if d == check_date:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    return streak


async def get_today_status(session: AsyncSession, user_id: str) -> list[tuple[Habit, bool]]:
    """Return list of (habit, is_completed_today) for a user."""
    habits = await list_habits(session, user_id)
    today = date.today()
    result: list[tuple[Habit, bool]] = []

    for habit in habits:
        completion = await session.execute(
            select(func.count()).where(
                HabitCompletion.habit_id == habit.id,
                HabitCompletion.date == today,
            )
        )
        done = completion.scalar_one() > 0
        result.append((habit, done))

    return result


async def get_best_streak(session: AsyncSession, habit_id: int) -> int:
    """Calculate the longest streak ever for a habit."""
    result = await session.execute(
        select(HabitCompletion.date)
        .where(HabitCompletion.habit_id == habit_id)
        .order_by(HabitCompletion.date)
    )
    dates = [row[0] for row in result.all()]

    if not dates:
        return 0

    best = 1
    current = 1
    for i in range(1, len(dates)):
        if dates[i] == dates[i - 1] + timedelta(days=1):
            current += 1
            best = max(best, current)
        else:
            current = 1

    return best


async def get_completion_rate(
    session: AsyncSession, habit_id: int, days: int
) -> tuple[int, int]:
    """Return (completed_days, total_days) for the last N days."""
    start_date = date.today() - timedelta(days=days - 1)
    result = await session.execute(
        select(func.count())
        .where(
            HabitCompletion.habit_id == habit_id,
            HabitCompletion.date >= start_date,
        )
    )
    completed = result.scalar_one()
    return completed, days


@dataclass
class HabitStats:
    name: str
    current_streak: int
    best_streak: int
    week_done: int
    week_total: int
    month_done: int
    month_total: int


async def get_habit_stats(session: AsyncSession, user_id: str) -> list[HabitStats]:
    """Get full stats for all habits of a user."""
    habits = await list_habits(session, user_id)
    stats: list[HabitStats] = []

    for habit in habits:
        current = await get_streak(session, habit.id)
        best = await get_best_streak(session, habit.id)
        week_done, week_total = await get_completion_rate(session, habit.id, 7)
        month_done, month_total = await get_completion_rate(session, habit.id, 30)

        stats.append(HabitStats(
            name=habit.name,
            current_streak=current,
            best_streak=best,
            week_done=week_done,
            week_total=week_total,
            month_done=month_done,
            month_total=month_total,
        ))

    return stats


async def get_weekly_summary(session: AsyncSession, user_id: str) -> str:
    """Generate a weekly summary text."""
    stats = await get_habit_stats(session, user_id)
    if not stats:
        return "No habits tracked yet."

    lines = ["📊 *Weekly Summary*\n"]
    total_done = 0
    total_possible = 0

    for s in stats:
        total_done += s.week_done
        total_possible += s.week_total
        pct = round(s.week_done / s.week_total * 100) if s.week_total > 0 else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"*{s.name}*")
        lines.append(f"  {bar} {s.week_done}/{s.week_total} ({pct}%)")
        lines.append(f"  Streak: {s.current_streak} days (best: {s.best_streak})")
        lines.append("")

    overall = round(total_done / total_possible * 100) if total_possible > 0 else 0
    lines.append(f"*Overall: {overall}%* ({total_done}/{total_possible})")

    return "\n".join(lines)


async def delete_habit(session: AsyncSession, habit_id: int) -> bool:
    habit = await session.get(Habit, habit_id)
    if habit is None:
        return False
    habit.is_active = False
    await session.commit()
    return True


async def update_job_id(session: AsyncSession, habit_id: int, job_id: str) -> None:
    habit = await session.get(Habit, habit_id)
    if habit:
        habit.apscheduler_job_id = job_id
        await session.commit()
