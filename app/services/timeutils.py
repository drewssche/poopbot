import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("CHAT_TIMEZONE", "Europe/Minsk"))


def now_local() -> datetime:
    return datetime.now(tz=TZ)


def today_local_date() -> date:
    return now_local().date()


def fmt_day(d: date) -> str:
    return d.strftime("%d.%m.%y")


def fmt_hhmm(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def calc_remind_at(now: datetime) -> datetime:
    """
    По умолчанию +2 часа, но не позже 23:55 локального времени.
    """
    target = now + timedelta(hours=2)

    # Кэп: 23:55 локального времени сегодняшнего дня
    cap = now.replace(hour=23, minute=55, second=0, microsecond=0)
    if target > cap:
        target = cap
    return target


def can_schedule_later(now: datetime) -> bool:
    # можно нажимать "позже" до 21:55 включительно
    return (now.astimezone(TZ).hour < 21) or (now.astimezone(TZ).hour == 21 and now.astimezone(TZ).minute <= 55)


def close_time_local(now: datetime) -> datetime:
    # 23:55 локального времени сегодня
    return now.astimezone(TZ).replace(hour=23, minute=55, second=0, microsecond=0)


def is_after_close(now: datetime) -> bool:
    return now >= close_time_local(now)
