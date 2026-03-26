from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Habit, Reminder


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
            buttons.append([InlineKeyboardButton(f"Done: {habit.name}", callback_data=f"noop_{habit.id}")])
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


def channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add WhatsApp", callback_data="add_whatsapp")],
        [InlineKeyboardButton("List channels", callback_data="list_channels")],
    ])
