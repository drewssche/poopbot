from __future__ import annotations

from datetime import date
from typing import Optional

from aiogram.utils.markdown import hbold

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionUserState


def format_user_mention(username: Optional[str], first_name: Optional[str], last_name: Optional[str], user_id: int) -> str:
    if username:
        return f"@{username}"
    # clickable mention by id:
    name = " ".join([p for p in [first_name, last_name] if p]) or f"User{user_id}"
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def render_q1_text(db: Session, chat_id: int, session_id: int, session_date: date) -> str:
    header = f"💩 Кто сегодня какал? ({session_date.strftime('%d.%m.%y')})"
    hint = "Чтобы попасть в список участников — нажми +1💩."

    # members from DB
    members = []
    # We will render in sort order later via caller passing in list; here just show states for chat members.
    # Fetch states for this session:
    states = {
        row.user_id: row
        for row in db.scalars(select(SessionUserState).where(SessionUserState.session_id == session_id)).all()
    }

    # We can't join to chat_members here without repeating repo logic; handler will supply members list.
    # So we render "empty" placeholder and let handler re-render with member lines.
    # We'll build lines in handler. This function returns header+hint and placeholder; handler will append lines.
    return f"{header}\n{hint}\n\n"


def render_member_lines(
    members: list[tuple[int, str, Optional[str], Optional[str], int]],  # (user_id, mention, achievement, emoji_remind, poops_n, bristol_emoji, feeling_emoji, streak)
) -> str:
    # Not used in this slice; kept for next step
    return ""
