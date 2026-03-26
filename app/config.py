from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///reminder_app.db"
    timezone: str = "UTC"

    # Optional: WhatsApp via Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_phone: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
