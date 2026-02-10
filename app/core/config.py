from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    log_level: str = "INFO"
    app_env: str = "dev"


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing in environment (.env)")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in environment (.env)")

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip(),
        app_env=os.getenv("APP_ENV", "dev").strip(),
    )
