import random
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomShameMessage, Habit, HabitCompletion

# Default escalating shame messages by level
DEFAULT_SHAME_MESSAGES: dict[str, list[str]] = {
    "gentle": [
        "Hey, your habit '{name}' is still waiting for you today...",
        "Psst... '{name}' called. It misses you.",
        "'{name}' is giving you the silent treatment. You know what to do.",
        "Just a friendly reminder that '{name}' exists. Unlike your motivation, apparently.",
        "'{name}' is starting to think you forgot about it.",
    ],
    "sarcasm": [
        "Day {days} without '{name}'. Your couch must be thrilled.",
        "'{name}'? Never heard of her. — You, apparently.",
        "Plot twist: '{name}' was optional all along. Oh wait, it wasn't.",
        "You and '{name}' are in a complicated relationship. Mostly you ghosting it.",
        "'{name}' is updating its resume. It's looking for someone more committed.",
    ],
    "dramatic": [
        "'{name}' has filed a missing person report for your discipline.",
        "Breaking news: Local person abandons '{name}' for {days} days. Experts baffled.",
        "'{name}' held a candlelight vigil for your consistency. It was very sad.",
        "Fun fact: you've spent {days} days NOT doing '{name}'. That's almost a talent.",
        "'{name}' asked me to tell you it's not angry, just disappointed. Very, very disappointed.",
    ],
    "nuclear": [
        "At this point, '{name}' is just a word in your vocabulary. And barely that.",
        "{days} days. '{name}' has moved on. It's seeing other people now.",
        "Your '{name}' streak died so long ago, archaeologists are studying it.",
        "Congratulations! You've set a personal record for ignoring '{name}'. {days} days and counting!",
        "'{name}' wanted me to tell you: 'We need to talk.' Just kidding. It gave up.",
        "I showed your '{name}' log to a therapist. They cried.",
    ],
}

VALID_LEVELS = list(DEFAULT_SHAME_MESSAGES.keys())


def get_shame_level(missed_days: int) -> str:
    if missed_days <= 1:
        return "gentle"
    elif missed_days <= 3:
        return "sarcasm"
    elif missed_days <= 7:
        return "dramatic"
    else:
        return "nuclear"


async def get_shame_message(session: AsyncSession, user_id: str, habit_name: str, missed_days: int) -> str:
    """Generate a shame message. Tries LLM first, falls back to static + custom pool."""
    level = get_shame_level(missed_days)

    # Try LLM-generated shame first
    from app.services.llm_service import generate_shame
    ai_message = await generate_shame(habit_name, missed_days, level)
    if ai_message:
        return ai_message

    # Fallback: static defaults + custom messages
    result = await session.execute(
        select(CustomShameMessage.message).where(
            CustomShameMessage.user_id == user_id,
            CustomShameMessage.level == level,
        )
    )
    custom = [row[0] for row in result.all()]

    all_messages = DEFAULT_SHAME_MESSAGES[level] + custom
    template = random.choice(all_messages)
    return template.format(name=habit_name, days=missed_days)


async def add_custom_shame(
    session: AsyncSession, user_id: str, level: str, message: str
) -> CustomShameMessage | None:
    """Add a custom shame message. Returns None if invalid level."""
    if level not in VALID_LEVELS:
        return None
    custom = CustomShameMessage(
        user_id=user_id,
        level=level,
        message=message,
    )
    session.add(custom)
    await session.commit()
    await session.refresh(custom)
    return custom


async def list_custom_shames(session: AsyncSession, user_id: str) -> list[CustomShameMessage]:
    result = await session.execute(
        select(CustomShameMessage)
        .where(CustomShameMessage.user_id == user_id)
        .order_by(CustomShameMessage.level, CustomShameMessage.id)
    )
    return list(result.scalars().all())


async def delete_custom_shame(session: AsyncSession, message_id: int) -> bool:
    msg = await session.get(CustomShameMessage, message_id)
    if msg is None:
        return False
    await session.delete(msg)
    await session.commit()
    return True


async def get_missed_days(session: AsyncSession, habit_id: int) -> int:
    """Count consecutive days without completion, ending at today."""
    today = date.today()
    result = await session.execute(
        select(HabitCompletion.date)
        .where(HabitCompletion.habit_id == habit_id)
        .order_by(HabitCompletion.date.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return 30  # Never completed — maximum shame

    last_done = row[0]
    missed = (today - last_done).days
    return max(missed, 0)


async def get_shameable_habits(session: AsyncSession, user_id: str) -> list[tuple[Habit, int]]:
    """Return habits with shame enabled that aren't completed today, with missed days."""
    today = date.today()
    habits = await session.execute(
        select(Habit).where(
            Habit.user_id == user_id,
            Habit.is_active == True,  # noqa: E712
            Habit.shame_enabled == True,  # noqa: E712
        )
    )

    result: list[tuple[Habit, int]] = []
    for (habit,) in habits.all():
        completion = await session.execute(
            select(HabitCompletion).where(
                HabitCompletion.habit_id == habit.id,
                HabitCompletion.date == today,
            )
        )
        if completion.scalar_one_or_none() is None:
            missed = await get_missed_days(session, habit.id)
            result.append((habit, missed))

    return result


async def toggle_shame(session: AsyncSession, habit_id: int) -> bool | None:
    """Toggle shame mode for a habit. Returns new state or None if not found."""
    habit = await session.get(Habit, habit_id)
    if habit is None:
        return None
    habit.shame_enabled = not habit.shame_enabled
    await session.commit()
    return habit.shame_enabled
