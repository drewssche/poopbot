from __future__ import annotations

import json
import math
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
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload, max(0, time.time() - path.stat().st_mtime)
    except Exception:
        return None, None


def heartbeat_failure_reason() -> str | None:
    heartbeat_interval_sec = env_int("HEARTBEAT_INTERVAL_SEC", 60)
    heartbeat_stale_sec = env_int("HEARTBEAT_STALE_SEC", 300)
    heartbeat_timeout_sec = env_int(
        "SUPERVISOR_HEARTBEAT_TIMEOUT_SEC",
        max(heartbeat_stale_sec + 120, heartbeat_interval_sec * 3),
    )

    payload, age_sec = read_heartbeat()
    if not isinstance(payload, dict) or age_sec is None:
        return "heartbeat_missing"
    if age_sec >= heartbeat_timeout_sec:
        return f"heartbeat_stale age={int(age_sec)} timeout={heartbeat_timeout_sec}"
    poll_age = payload.get("last_poll_success_sec")
    if poll_age is not None:
        try:
            poll_age = float(poll_age) + age_sec
        except (TypeError, ValueError):
            return "heartbeat_invalid"
        if not math.isfinite(poll_age) or poll_age < 0:
            return "heartbeat_invalid"
        if poll_age >= heartbeat_timeout_sec:
            return f"polling_stale age={int(poll_age)} timeout={heartbeat_timeout_sec}"
    return None
