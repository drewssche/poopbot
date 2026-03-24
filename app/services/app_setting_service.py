from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AppSetting


RESTORE_CLAIM_ENABLED_KEY = "restore_claim_enabled"


def get_setting(db: Session, key: str) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row is not None else None


def set_setting(db: Session, key: str, value: str | None) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
        return
    row.value = value


def get_bool_setting(db: Session, key: str, *, default: bool = False) -> bool:
    raw = get_setting(db, key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def set_bool_setting(db: Session, key: str, value: bool) -> None:
    set_setting(db, key, "true" if value else "false")
