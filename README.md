# PingMe - Reminder & Habit Tracker Bot

A self-hosted Telegram bot for managing reminders and tracking daily habits with streaks, analytics, and multi-channel notifications.

## Features

- **Reminders** — one-time (`30m`, `2h`, specific date) and recurring (cron expressions)
- **Habit Tracker** — daily/weekly habits with completion tracking and streak calculation
- **Snooze/Dismiss** — reminders auto-repeat up to 3 times if not dismissed, with snooze buttons (5m/15m)
- **Analytics** — weekly/monthly completion rates, current vs best streak, progress bars
- **Daily Dashboard** — `/today` shows all habits and reminders at a glance
- **Weekly Summary** — auto-sent every Sunday at 8 PM with your week's scorecard
- **Multi-channel Notifications** — Telegram (built-in) + WhatsApp via Twilio

## Bot Commands

| Command | Description |
|---------|-------------|
| `/today` | Your day at a glance |
| `/remind` | Create a new reminder (guided) |
| `/reminders` | List & delete active reminders |
| `/habit` | Create a new habit (guided) |
| `/habits` | Today's habit status + mark done |
| `/streak` | View habit streaks |
| `/stats` | Habit analytics and completion rates |
| `/deletehabit` | Delete a habit |
| `/channels` | Manage notification channels |
| `/help` | Show all commands |

## Setup

### 1. Get a Telegram Bot Token

Open Telegram, search for [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the prompts.

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your bot token:

```
TELEGRAM_BOT_TOKEN=your-bot-token-here
TIMEZONE=Europe/Madrid
```

### 3. Run with Docker

```bash
docker compose up -d
```

### 3b. Run Locally (alternative)

```bash
uv sync
uv run reminder-app
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `TIMEZONE` | No | `UTC` | Your timezone (e.g. `Europe/Madrid`) |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///reminder_app.db` | Database connection string |
| `TWILIO_ACCOUNT_SID` | No | — | For WhatsApp notifications |
| `TWILIO_AUTH_TOKEN` | No | — | For WhatsApp notifications |
| `TWILIO_FROM_PHONE` | No | — | For WhatsApp notifications |

## Tech Stack

- **Python 3.13** with async throughout
- **python-telegram-bot** — Telegram bot interface
- **SQLAlchemy** (async) + **SQLite** — storage
- **APScheduler** — reminder scheduling
- **Apprise** — multi-channel notification support
- **uv** — package management
- **ty** — type checking
- **Docker Compose** — deployment
