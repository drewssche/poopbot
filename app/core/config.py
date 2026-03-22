from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    log_level: str = "INFO"
    app_env: str = "dev"
    bot_owner_id: int | None = None
    db_pool_size: int = 4
    db_max_overflow: int = 2
    db_pool_timeout_sec: int = 10
    db_pool_recycle_sec: int = 1800
    startup_delete_webhook: bool = True
    drop_pending_updates_on_start: bool = False
    startup_recover_missing_q1: bool = True
    heartbeat_interval_sec: int = 60
    heartbeat_stale_sec: int = 300
    polling_guard_interval_sec: int = 60
    polling_guard_request_timeout_sec: int = 10
    polling_guard_network_failures_to_restart: int = 5
    scheduler_chat_throttle_sec: float = 0.2
    scheduler_tick_interval_sec: int = 60
    scheduler_q1_catchup_max_delay_min: int = 180
    webhook_guard_enabled: bool = True
    webhook_guard_interval_sec: int = 180
    polling_guard_enabled: bool = True
    polling_guard_pending_threshold: int = 5
    handled_rate_log_interval_sec: int = 0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _env_non_negative_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value >= 0 else default
    except ValueError:
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
        db_pool_size=_env_int("DB_POOL_SIZE", 4),
        db_max_overflow=_env_non_negative_int("DB_MAX_OVERFLOW", 2),
        db_pool_timeout_sec=_env_int("DB_POOL_TIMEOUT_SEC", 10),
        db_pool_recycle_sec=_env_int("DB_POOL_RECYCLE_SEC", 1800),
        startup_delete_webhook=_env_bool("STARTUP_DELETE_WEBHOOK", True),
        drop_pending_updates_on_start=_env_bool("DROP_PENDING_UPDATES_ON_START", False),
        startup_recover_missing_q1=_env_bool("STARTUP_RECOVER_MISSING_Q1", True),
        heartbeat_interval_sec=_env_int("HEARTBEAT_INTERVAL_SEC", 60),
        heartbeat_stale_sec=_env_int("HEARTBEAT_STALE_SEC", 300),
        polling_guard_interval_sec=_env_int("POLLING_GUARD_INTERVAL_SEC", 60),
        polling_guard_request_timeout_sec=_env_int("POLLING_GUARD_REQUEST_TIMEOUT_SEC", 10),
        polling_guard_network_failures_to_restart=_env_int("POLLING_GUARD_NETWORK_FAILURES_TO_RESTART", 5),
        scheduler_chat_throttle_sec=_env_float("SCHEDULER_CHAT_THROTTLE_SEC", 0.2),
        scheduler_tick_interval_sec=_env_int("SCHEDULER_TICK_INTERVAL_SEC", 60),
        scheduler_q1_catchup_max_delay_min=_env_int("SCHEDULER_Q1_CATCHUP_MAX_DELAY_MIN", 180),
        webhook_guard_enabled=_env_bool("WEBHOOK_GUARD_ENABLED", True),
        webhook_guard_interval_sec=_env_int("WEBHOOK_GUARD_INTERVAL_SEC", 180),
        polling_guard_enabled=_env_bool("POLLING_GUARD_ENABLED", True),
        polling_guard_pending_threshold=_env_non_negative_int("POLLING_GUARD_PENDING_THRESHOLD", 5),
        handled_rate_log_interval_sec=_env_non_negative_int("HANDLED_RATE_LOG_INTERVAL_SEC", 0),
    )
