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


# =============================================================================
# Временные слоты
# =============================================================================

TIME_SLOTS = {
    "night": "🌙",
    "morning": "🌅",
    "afternoon": "☀️",
    "evening": "🌆",
}

SLOT_TITLES = {
    "night": "Ночной серун",
    "morning": "Утренний жаворонок",
    "afternoon": "Дневной трудяга",
    "evening": "Вечерний философ",
}


def get_time_slot(dt: datetime, tz_name: str = "Europe/Minsk") -> str:
    """Определяет временной слот по времени."""
    local_dt = dt.astimezone(pytz.timezone(tz_name))
    hour = local_dt.hour
    
    if 0 <= hour < 6:
        return "night"
    elif 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    else:
        return "evening"


def get_slot_emoji(slot: str) -> str:
    """Возвращает эмодзи временного слота."""
    return TIME_SLOTS.get(slot, "⏰")


def get_slot_title(slot: str) -> str:
    """Возвращает название титула слота."""
    return SLOT_TITLES.get(slot, "Участник")


def get_dominant_slot(slot_counts: dict[str, int]) -> str | None:
    """Определяет доминирующий слот по счётчикам."""
    if not slot_counts or all(v == 0 for v in slot_counts.values()):
        return None
    
    # Проверяем на равенство всех слотов (Круглосуточный)
    non_zero_slots = [k for k, v in slot_counts.items() if v > 0]
    if len(non_zero_slots) == 4:
        values = [slot_counts[s] for s in non_zero_slots]
        if max(values) - min(values) <= 1:  # Разница не больше 1
            return "all_day"
    
    # Возвращаем слот с максимальным значением
    return max(slot_counts.keys(), key=lambda s: slot_counts.get(s, 0))


def get_slot_popup(slot: str, hour: int) -> str:
    """Возвращает текст попапа для слота."""
    popups = {
        "night": "Ночной серун 🌙",
        "morning": "Кофе с сигаркой ☕" if hour < 9 else "Доброе утро 🌅",
        "afternoon": "После обеда 🍽️" if hour < 14 else "Дневной сеанс ☀️",
        "evening": "Вечерний ритуал 🌆" if hour < 21 else "Почти ночь 🌙",
    }
    return popups.get(slot, "")
