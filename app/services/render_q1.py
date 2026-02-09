from datetime import date, datetime
from typing import Dict, Tuple, Optional, List

from app.services.timeutils import fmt_day, fmt_hhmm

# statuses: user_id -> ("poop"/"no"/"later"/None, remind_at_datetime_or_None)


def render_q1(
    day: date,
    lines: List[Tuple[str, str]],
) -> str:
    """
    lines: list of tuples (mention_text, status_and_streak_text)
    """
    header = f"💩 Кто сегодня какал? ({fmt_day(day)})\n\n"
    body = "\n".join([f"- {m} — {rest}" for (m, rest) in lines])
    return header + body


def status_text(answer: Optional[str], remind_at: Optional[datetime]) -> str:
    if answer is None:
        return "❓"
    if answer == "poop":
        return "💩"
    if answer == "no":
        return "❌"
    if answer == "later":
        if remind_at is not None:
            return f"⏳ Напомню в {fmt_hhmm(remind_at)}"
        return "⏳"
    return "❓"


def streak_text(streak_days: int, streak_start: Optional[date]) -> str:
    if streak_days <= 0:
        return "стрик 0"
    # формат: "стрик дней N — с ДД.ММ.ГГ"
    return f"стрик дней {streak_days} — с {streak_start.strftime('%d.%m.%y')}"
