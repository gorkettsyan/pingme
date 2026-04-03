from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Goal, Habit, Reminder


def reminder_list_keyboard(reminders: list[Reminder]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"Delete: {r.title}", callback_data=f"del_rem_{r.id}")]
        for r in reminders
    ]
    return InlineKeyboardMarkup(buttons)


def habit_list_keyboard(habits: list[tuple[Habit, bool]]) -> InlineKeyboardMarkup:
    buttons = []
    for habit, done in habits:
        if done:
            buttons.append([InlineKeyboardButton(f"✅ Undo: {habit.name}", callback_data=f"undone_{habit.id}")])
        else:
            buttons.append([InlineKeyboardButton(f"Mark done: {habit.name}", callback_data=f"done_{habit.id}")])
    return InlineKeyboardMarkup(buttons)


def habit_delete_keyboard(habits: list[Habit]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"Delete: {h.name}", callback_data=f"del_hab_{h.id}")]
        for h in habits
    ]
    return InlineKeyboardMarkup(buttons)


def shame_toggle_keyboard(habits: list[Habit]) -> InlineKeyboardMarkup:
    buttons = []
    for h in habits:
        icon = "🔕 Disable" if h.shame_enabled else "😈 Enable"
        buttons.append([InlineKeyboardButton(f"{icon}: {h.name}", callback_data=f"shame_{h.id}")])
    return InlineKeyboardMarkup(buttons)


def habit_edit_keyboard(habits: list[Habit]) -> InlineKeyboardMarkup:
    buttons = []
    for h in habits:
        time_str = h.reminder_time.strftime("%H:%M") if h.reminder_time else "not set"
        buttons.append([InlineKeyboardButton(
            f"{h.name} ({time_str})", callback_data=f"edit_hab_{h.id}"
        )])
    return InlineKeyboardMarkup(buttons)


def goal_list_keyboard(goals: list[tuple[Goal, int, int]]) -> InlineKeyboardMarkup:
    """Keyboard for /goals: log progress or view details."""
    buttons = []
    for goal, today_count, total in goals:
        pct = round(total / goal.target_count * 100) if goal.target_count > 0 else 0
        buttons.append([InlineKeyboardButton(
            f"+{goal.daily_quota}: {goal.name} ({total}/{goal.target_count} — {pct}%)",
            callback_data=f"goal_log_{goal.id}",
        )])
    return InlineKeyboardMarkup(buttons)


def goal_delete_keyboard(goals: list[Goal]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"Delete: {g.name}", callback_data=f"del_goal_{g.id}")]
        for g in goals
    ]
    return InlineKeyboardMarkup(buttons)


def channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add WhatsApp", callback_data="add_whatsapp")],
        [InlineKeyboardButton("List channels", callback_data="list_channels")],
    ])
