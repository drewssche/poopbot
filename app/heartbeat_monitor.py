from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def heartbeat_path() -> Path:
    return Path(tempfile.gettempdir()) / "poopbot_heartbeat.json"


def env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def read_heartbeat() -> tuple[dict | None, float | None]:
    path = heartbeat_path()
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    return payload, time.time() - path.stat().st_mtime


def heartbeat_failure_reason() -> str | None:
    heartbeat_interval_sec = env_int("HEARTBEAT_INTERVAL_SEC", 60)
    heartbeat_stale_sec = env_int("HEARTBEAT_STALE_SEC", 300)
    heartbeat_timeout_sec = env_int(
        "SUPERVISOR_HEARTBEAT_TIMEOUT_SEC",
        max(heartbeat_stale_sec + 120, heartbeat_interval_sec * 3),
    )
    max_idle_sec = env_int("SUPERVISOR_MAX_IDLE_SEC", 43200)

    payload, age_sec = read_heartbeat()
    if payload is None or age_sec is None:
        return "heartbeat_missing"
    if age_sec >= heartbeat_timeout_sec:
        return f"heartbeat_stale age={int(age_sec)} timeout={heartbeat_timeout_sec}"
    if max_idle_sec > 0:
        last_received = int(payload.get("last_received_sec", 0) or 0)
        last_handled = int(payload.get("last_handled_sec", 0) or 0)
        if last_received >= max_idle_sec and last_handled >= max_idle_sec:
            return (
                "polling_idle "
                f"last_received={last_received} last_handled={last_handled} limit={max_idle_sec}"
            )
    return None
