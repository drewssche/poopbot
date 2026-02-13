from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    log_level: str = "INFO"
    app_env: str = "dev"
    bot_owner_id: int | None = None
    startup_delete_webhook: bool = True
    drop_pending_updates_on_start: bool = False
    heartbeat_interval_sec: int = 60
    heartbeat_stale_sec: int = 300
    scheduler_chat_throttle_sec: float = 0.2


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value >= 0 else default
    except ValueError:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


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
        bot_owner_id=int(owner) if (owner := os.getenv("BOT_OWNER_ID", "").strip()).isdigit() else None,
        startup_delete_webhook=_env_bool("STARTUP_DELETE_WEBHOOK", True),
        drop_pending_updates_on_start=_env_bool("DROP_PENDING_UPDATES_ON_START", False),
        heartbeat_interval_sec=_env_int("HEARTBEAT_INTERVAL_SEC", 60),
        heartbeat_stale_sec=_env_int("HEARTBEAT_STALE_SEC", 300),
        scheduler_chat_throttle_sec=_env_float("SCHEDULER_CHAT_THROTTLE_SEC", 0.2),
    )
