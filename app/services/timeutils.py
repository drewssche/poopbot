import os
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo

# TZ всего бота (пока один на всё, как у тебя сейчас)
TZ = ZoneInfo(os.getenv("CHAT_TIMEZONE", "Europe/Minsk"))

# Закрываем день чуть раньше полуночи, чтобы не ловить пограничные клики
CLOSE_HOUR = 23
CLOSE_MINUTE = 55

# Последний момент, когда вообще можно жать “напомнить …”
# (по старой договорённости — чтобы напоминание/действие не уходило в закрытие)
LAST_ACTION_HOUR = 21
LAST_ACTION_MINUTE = 55


def now_local() -> datetime:
    return datetime.now(tz=TZ)


def today_local_date() -> date:
    return now_local().date()


def fmt_day(d: date) -> str:
    return d.strftime("%d.%m.%y")


def fmt_hhmm(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def close_time_local(now: datetime | None = None) -> datetime:
    """
    23:55 локального времени (сегодня по TZ).
    """
    n = now.astimezone(TZ) if now else now_local()
    return n.replace(hour=CLOSE_HOUR, minute=CLOSE_MINUTE, second=0, microsecond=0)


def is_after_close(now: datetime) -> bool:
    return now >= close_time_local(now)


def last_action_deadline_local(now: datetime | None = None) -> datetime:
    """
    21:55 локального времени (сегодня по TZ) — после этого блокируем “напомнить/клик”.
    """
    n = now.astimezone(TZ) if now else now_local()
    return n.replace(hour=LAST_ACTION_HOUR, minute=LAST_ACTION_MINUTE, second=0, microsecond=0)


def can_interact_today(now: datetime) -> bool:
    """
    Можно ли вообще нажимать кнопки сегодня (до 23:55).
    """
    return not is_after_close(now)


def can_schedule_endday_reminder(now: datetime) -> bool:
    """
    Можно ли нажать “⏳ Напомнить в конце дня (22:00)”.
    По твоей логике: последний клик — до 21:55.
    """
    return now < last_action_deadline_local(now)


def remind_end_of_day_at(now: datetime, hour: int = 22, minute: int = 0) -> datetime:
    """
    Возвращает remind_at для “в конце дня” (22:00 локального времени), но:
    - не позже закрытия дня (23:55)
    - всегда today по TZ

    Важно: возвращаем datetime с TZ (локальный), ты дальше можешь хранить как timestamptz (Postgres сам).
    """
    n = now.astimezone(TZ)
    target = n.replace(hour=hour, minute=minute, second=0, microsecond=0)

    cap = close_time_local(n)
    if target > cap:
        target = cap
    return target


def post_time_local_for_day(day: date, hour: int) -> datetime:
    """
    Локальное время постинга (например 10:00/14:00/20:00) для конкретного day.
    """
    return datetime.combine(day, time(hour=hour, minute=0), tzinfo=TZ)


def is_time_to_post(now: datetime, post_hour: int, window_minutes: int = 3) -> bool:
    """
    True если сейчас “окно” постинга для этого часа.
    Например, scheduler тикает каждые 30с: мы считаем “пора”, когда
    local_time ∈ [HH:00, HH:00+window)
    """
    n = now.astimezone(TZ)
    start = n.replace(hour=post_hour, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=window_minutes)
    return start <= n < end
