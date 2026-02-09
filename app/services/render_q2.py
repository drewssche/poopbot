from datetime import date
from typing import Optional, List, Tuple

from app.services.timeutils import fmt_day


def status_q2_text(answer: Optional[str]) -> str:
    if answer is None:
        return "❓"
    if answer == "good":
        return "😇"
    if answer == "ok":
        return "😐"
    if answer == "bad":
        return "😫"
    return "❓"


def render_q2(day: date, lines: List[Tuple[str, str]]) -> str:
    header = f"😮‍💨 Как прошёл процесс? ({fmt_day(day)})\n\n"
    body = "\n".join([f"- {m} — {st}" for (m, st) in lines])
    return header + body
