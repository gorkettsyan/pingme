from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Goal, GoalProgress


async def create_goal(
    session: AsyncSession,
    user_id: str,
    name: str,
    target_count: int,
    daily_quota: int = 1,
    deadline: date | None = None,
    reminder_time: time | None = None,
    description: str | None = None,
) -> Goal:
    goal = Goal(
        user_id=user_id,
        name=name,
        description=description,
        target_count=target_count,
        daily_quota=daily_quota,
        deadline=deadline,
        reminder_time=reminder_time,
        is_active=True,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def list_goals(session: AsyncSession, user_id: str) -> list[Goal]:
    result = await session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.is_active == True).order_by(Goal.created_at)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_goal(session: AsyncSession, goal_id: int) -> Goal | None:
    return await session.get(Goal, goal_id)


async def log_progress(
    session: AsyncSession,
    goal_id: int,
    count: int = 1,
    for_date: date | None = None,
) -> GoalProgress:
    """Log progress for a goal on a given date. Adds to existing count if already logged."""
    target_date = for_date or date.today()

    existing = await session.execute(
        select(GoalProgress).where(
            GoalProgress.goal_id == goal_id,
            GoalProgress.date == target_date,
        )
    )
    progress = existing.scalar_one_or_none()

    if progress is not None:
        progress.count += count
    else:
        progress = GoalProgress(
            goal_id=goal_id,
            count=count,
            date=target_date,
            created_at=datetime.now(),
        )
        session.add(progress)

    await session.commit()
    await session.refresh(progress)
    return progress


async def get_total_progress(session: AsyncSession, goal_id: int) -> int:
    """Get total count across all days for a goal."""
    result = await session.execute(
        select(func.coalesce(func.sum(GoalProgress.count), 0)).where(
            GoalProgress.goal_id == goal_id,
        )
    )
    return result.scalar_one()


async def get_today_progress(session: AsyncSession, goal_id: int) -> int:
    """Get today's progress count for a goal."""
    result = await session.execute(
        select(func.coalesce(func.sum(GoalProgress.count), 0)).where(
            GoalProgress.goal_id == goal_id,
            GoalProgress.date == date.today(),
        )
    )
    return result.scalar_one()


async def get_streak(session: AsyncSession, goal_id: int) -> int:
    """Calculate current streak of consecutive days where daily quota was met."""
    goal = await session.get(Goal, goal_id)
    if not goal:
        return 0

    result = await session.execute(
        select(GoalProgress.date, GoalProgress.count)
        .where(GoalProgress.goal_id == goal_id)
        .order_by(GoalProgress.date.desc())
    )
    rows = result.all()

    if not rows:
        return 0

    streak = 0
    check_date = date.today()
    # Allow today to still be in progress
    if not rows or rows[0][0] != check_date:
        check_date = check_date - timedelta(days=1)
        if not rows or rows[0][0] != check_date:
            return 0

    for d, count in rows:
        if d == check_date and count >= goal.daily_quota:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    return streak


async def get_today_status(session: AsyncSession, user_id: str) -> list[tuple[Goal, int, int]]:
    """Return list of (goal, today_count, total_count) for a user."""
    goals = await list_goals(session, user_id)
    result: list[tuple[Goal, int, int]] = []

    for goal in goals:
        today_count = await get_today_progress(session, goal.id)
        total = await get_total_progress(session, goal.id)
        result.append((goal, today_count, total))

    return result


@dataclass
class GoalStats:
    name: str
    target_count: int
    daily_quota: int
    total_done: int
    today_done: int
    current_streak: int
    completion_pct: float
    days_active: int
    deadline: date | None
    projected_days_left: int | None  # estimated days to finish at current pace


async def get_goal_stats(session: AsyncSession, user_id: str) -> list[GoalStats]:
    """Get full stats for all goals of a user."""
    goals = await list_goals(session, user_id)
    stats: list[GoalStats] = []

    for goal in goals:
        total = await get_total_progress(session, goal.id)
        today = await get_today_progress(session, goal.id)
        streak = await get_streak(session, goal.id)

        days_active = (date.today() - goal.created_at.date()).days + 1
        completion_pct = round(total / goal.target_count * 100, 1) if goal.target_count > 0 else 0

        # Project remaining days based on average daily pace
        remaining = goal.target_count - total
        if remaining <= 0:
            projected = 0
        elif days_active > 0 and total > 0:
            avg_per_day = total / days_active
            projected = ceil(remaining / avg_per_day)
        else:
            projected = ceil(remaining / goal.daily_quota) if goal.daily_quota > 0 else None

        stats.append(GoalStats(
            name=goal.name,
            target_count=goal.target_count,
            daily_quota=goal.daily_quota,
            total_done=total,
            today_done=today,
            current_streak=streak,
            completion_pct=completion_pct,
            days_active=days_active,
            deadline=goal.deadline,
            projected_days_left=projected,
        ))

    return stats


async def delete_goal(session: AsyncSession, goal_id: int) -> bool:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        return False
    goal.is_active = False
    await session.commit()
    return True


async def update_job_id(session: AsyncSession, goal_id: int, job_id: str) -> None:
    goal = await session.get(Goal, goal_id)
    if goal:
        goal.apscheduler_job_id = job_id
        await session.commit()
