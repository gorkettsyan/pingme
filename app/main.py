import logging

from telegram.ext import Application

from app.bot.handlers import get_handlers
from app.config import settings
from app.db import init_db
from app.scheduler import restore_jobs, schedule_shame_check, schedule_weekly_summary, scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    await init_db()
    scheduler.start()
    schedule_weekly_summary()
    schedule_shame_check()
    await restore_jobs()
    logger.info("Database initialized and scheduler started")


async def post_shutdown(application: Application) -> None:  # type: ignore[type-arg]
    scheduler.shutdown()
    logger.info("Scheduler shut down")


def main() -> None:
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")
        return

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    for handler in get_handlers():
        application.add_handler(handler)

    logger.info("Starting bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
