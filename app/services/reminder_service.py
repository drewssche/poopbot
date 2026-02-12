from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CommandMessage, Session as DaySession, SessionUserState, User
from app.services.command_message_service import set_command_message_id

REMINDER22_COMMAND = "reminder22"
REMINDER22_ACK_COMMAND = "reminder22_ack"


def mark_reminder_ack(db: Session, chat_id: int, user_id: int, session_date: date, message_id: int) -> None:
    set_command_message_id(
        db,
        chat_id=chat_id,
        user_id=user_id,
        command=REMINDER22_ACK_COMMAND,
        session_date=session_date,
        message_id=message_id,
    )


def build_reminder_22_text(db: Session, session_id: int) -> str | None:
    sess = db.get(DaySession, session_id)
    if sess is None:
        return None

    rows = db.scalars(
        select(SessionUserState)
        .where(SessionUserState.session_id == session_id, SessionUserState.remind_22 == True)  # noqa: E712
        .order_by(SessionUserState.user_id.asc())
    ).all()
    if not rows:
        return None

    user_ids = [r.user_id for r in rows]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}

    acked_user_ids = set(
        db.scalars(
            select(CommandMessage.user_id).where(
                CommandMessage.chat_id == sess.chat_id,
                CommandMessage.command == REMINDER22_ACK_COMMAND,
                CommandMessage.session_date == sess.session_date,
            )
        ).all()
    )

    lines = ["⏰ А вот и 22:00. Ну что ребята, покакали?"]
    for st in rows:
        u = users.get(st.user_id)
        status = "✅" if int(st.user_id) in acked_user_ids else "❓"
        poops = int(st.poops_n or 0)

        if u and u.username:
            who = f"@{u.username}"
        else:
            first = (u.first_name if u else "") or ""
            last = (u.last_name if u else "") or ""
            full_name = " ".join(x.strip() for x in (first, last) if x and x.strip()).strip() or "Пользователь"
            who = f'<a href="tg://user?id={st.user_id}">{full_name}</a>'

        lines.append(f"{who} {status} 💩({poops})")

    return "\n".join(lines)
