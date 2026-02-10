from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time
import pytz


@dataclass(frozen=True)
class SessionWindow:
    session_date: date
    is_blocked_window: bool  # 23:55–00:05
    is_active_window: bool   # 00:05–23:55


def now_in_tz(tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def get_session_window(tz_name: str) -> SessionWindow:
    now = now_in_tz(tz_name)
    t = now.timetz()

    start = time(0, 5, 0, tzinfo=t.tzinfo)
    end = time(23, 55, 0, tzinfo=t.tzinfo)

    if t < start:
        # 00:00–00:05 => blocked, current session not started yet
        return SessionWindow(session_date=now.date(), is_blocked_window=True, is_active_window=False)
    if t >= end:
        # 23:55–24:00 => blocked
        return SessionWindow(session_date=now.date(), is_blocked_window=True, is_active_window=False)

    return SessionWindow(session_date=now.date(), is_blocked_window=False, is_active_window=True)
