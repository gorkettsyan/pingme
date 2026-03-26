import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SHAME_PROMPT = """\
You are a savage comedy roast writer. Someone skipped "{name}" for {days} days. \
Write an original, unique, funny roast at the "{level}" level.

Levels explained:
- gentle = playful teasing
- sarcasm = passive-aggressive wit
- dramatic = soap opera melodrama
- nuclear = absolutely ruthless mockery

Rules:
- Write ONE short roast (1-2 sentences)
- Be ORIGINAL. Do not copy examples. Create something NEW
- Mock their laziness, not them as a person
- No emojis. No apologies. No encouragement. Just roast them.
- Output ONLY the roast, nothing else\
"""

PARSE_PROMPT = """\
You are a reminder parser. Convert natural language into a structured JSON reminder.

Current date/time: {now}
Timezone: {timezone}

Return ONLY valid JSON with these fields:
- "title": short reminder title
- "time_type": "relative" or "absolute" or "cron"
- "relative_minutes": number of minutes from now (for relative)
- "absolute_time": "YYYY-MM-DD HH:MM" in user's timezone (for absolute)
- "cron_expression": 5-field cron expression (for recurring)

IMPORTANT: "1 minute" = 1, "5 minutes" = 5, "1 hour" = 60, "2 hours" = 120. Do NOT confuse minutes and hours.

Examples:
- "test in 1 minute" -> {{"title": "Test", "time_type": "relative", "relative_minutes": 1}}
- "call mom in 30 minutes" -> {{"title": "Call mom", "time_type": "relative", "relative_minutes": 30}}
- "check oven in 2 hours" -> {{"title": "Check oven", "time_type": "relative", "relative_minutes": 120}}
- "take medicine every day at 9am" -> {{"title": "Take medicine", "time_type": "cron", "cron_expression": "0 9 * * *"}}
- "meeting tomorrow at 2pm" -> {{"title": "Meeting", "time_type": "absolute", "absolute_time": "2026-03-27 14:00"}}
- "water plants every monday and thursday at 8am" -> {{"title": "Water plants", "time_type": "cron", "cron_expression": "0 8 * * mon,thu"}}
- "remind me every weekday at 9am to check emails" -> {{"title": "Check emails", "time_type": "cron", "cron_expression": "0 9 * * mon-fri"}}

Return ONLY valid JSON, no markdown, no explanation.\
"""


async def _call_ollama(prompt: str, system: str = "", temperature: float = 0.7) -> str | None:
    """Call Ollama API. Returns None if unavailable."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": 200},
                },
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "").strip()
    except Exception:
        logger.debug("Ollama unavailable or failed")
        return None


async def is_available() -> bool:
    """Check if Ollama is running and the model is loaded."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.ollama_url}/api/tags")
            response.raise_for_status()
            return True
    except Exception:
        return False


async def generate_shame(habit_name: str, missed_days: int, level: str) -> str | None:
    """Generate a shame message using LLM. Returns None if unavailable."""
    prompt = SHAME_PROMPT.format(name=habit_name, days=missed_days, level=level)
    result = await _call_ollama(prompt, temperature=0.9)
    if result:
        # Clean up any quotes the model might wrap the message in
        result = result.strip('"').strip("'")
        logger.info("LLM generated shame: %s", result)
    return result


PRAISE_PROMPT = """\
Someone just completed their habit "{name}"! Their current streak is {streak} day(s).

Write a short, enthusiastic congratulatory message. Be creative and fun.

Style based on streak:
- 1-3 days: encouraging, "good start" energy
- 4-7 days: excited, building momentum
- 8-14 days: impressed, they're on fire
- 15-30 days: amazed, legendary status
- 30+ days: worship them, they're a machine

Rules:
- ONE message, 1-2 sentences max
- Be ORIGINAL and creative each time
- Reference the habit name and streak naturally
- Be genuinely encouraging, not sarcastic
- No emojis. Output ONLY the message.\
"""

DEFAULT_PRAISE: dict[str, list[str]] = {
    "starter": [
        "You showed up for '{name}' today. That's what winners do.",
        "Day {streak} of '{name}' in the bag. The hardest part is starting, and you did it.",
        "'{name}' done. Small steps, big results. Keep going.",
    ],
    "building": [
        "{streak} days of '{name}'! You're building something here.",
        "'{name}' streak at {streak} days. Momentum is a beautiful thing.",
        "Look at you, {streak} days strong on '{name}'. This is becoming a habit for real.",
    ],
    "impressive": [
        "{streak} days of '{name}'! You're officially unstoppable.",
        "'{name}' for {streak} days straight? That's not luck, that's discipline.",
        "{streak} days! '{name}' isn't a habit anymore, it's who you are.",
    ],
    "legendary": [
        "{streak} days of '{name}'. You're not human, you're a machine.",
        "'{name}' for {streak} days? Someone call the record books.",
        "{streak} days! At this point '{name}' should be paying YOU.",
    ],
}


def _get_praise_level(streak: int) -> str:
    if streak <= 3:
        return "starter"
    elif streak <= 7:
        return "building"
    elif streak <= 14:
        return "impressive"
    else:
        return "legendary"


async def generate_praise(habit_name: str, streak: int) -> str:
    """Generate a praise message. Tries LLM first, falls back to static."""
    import random

    # Try LLM first
    prompt = PRAISE_PROMPT.format(name=habit_name, streak=streak)
    result = await _call_ollama(prompt, temperature=0.9)
    if result:
        result = result.strip('"').strip("'")
        logger.info("LLM generated praise: %s", result)
        return result

    # Fallback to static
    level = _get_praise_level(streak)
    template = random.choice(DEFAULT_PRAISE[level])
    return template.format(name=habit_name, streak=streak)


ROUTER_PROMPT = """\
You are a smart assistant for a reminder and habit tracking bot. \
Classify the user's message into one of these intents:

- "reminder": user wants to create a reminder (e.g. "remind me to call mom tomorrow", "buy groceries in 30 min")
- "habit_done": user is saying they completed a habit (e.g. "I did my exercise", "finished reading")
- "question": user is asking a question or wants information (e.g. "what habits do I have?", "how many days is my streak?")
- "chat": user is just chatting, greeting, or saying something unrelated

Return ONLY a JSON object with:
- "intent": one of "reminder", "habit_done", "question", "chat"
- "response": a short helpful response for "question" or "chat" intents (1-2 sentences)
- "habit_name": the habit name if intent is "habit_done" (or null)

For "reminder" intent, set response to null — the reminder parser will handle it.

The user has these habits: {habits}
Current date/time: {now}

Return ONLY valid JSON, no markdown.\
"""


async def classify_intent(text: str, habits: list[str]) -> dict[str, str | None] | None:
    """Classify user message intent. Returns None if LLM unavailable."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %A")
    habits_str = ", ".join(habits) if habits else "none yet"

    system = ROUTER_PROMPT.format(habits=habits_str, now=now)
    result = await _call_ollama(text, system=system, temperature=0.1)

    if not result:
        return None

    try:
        if result.startswith("```"):
            result = result.split("\n", 1)[1] if "\n" in result else result[3:]
            result = result.rsplit("```", 1)[0]

        parsed: dict[str, str | None] = json.loads(result.strip())
        logger.info("LLM classified: %s -> %s", text, parsed)
        return parsed
    except (json.JSONDecodeError, KeyError):
        logger.warning("LLM returned invalid classification: %s", result)
        return None


async def parse_natural_language(text: str) -> dict[str, str | int | None] | None:
    """Parse natural language reminder using LLM. Returns None if unavailable."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M %A")

    system = PARSE_PROMPT.format(now=now, timezone=settings.timezone)
    result = await _call_ollama(text, system=system, temperature=0.1)

    if not result:
        return None

    try:
        # Strip markdown code fences if present
        if result.startswith("```"):
            result = result.split("\n", 1)[1] if "\n" in result else result[3:]
            result = result.rsplit("```", 1)[0]

        parsed: dict[str, str | int | None] = json.loads(result.strip())
        logger.info("LLM parsed: %s -> %s", text, parsed)
        return parsed
    except (json.JSONDecodeError, KeyError):
        logger.warning("LLM returned invalid JSON: %s", result)
        return None
