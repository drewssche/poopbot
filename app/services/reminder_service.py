from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMember, CommandMessage, Session as DaySession, SessionUserState, User
from app.services.command_message_service import set_command_message_id

REMINDER22_COMMAND = "reminder22"
REMINDER22_ACK_COMMAND = "reminder22_ack"
LATE_REMINDER_COMMAND = "late_reminder"


def _user_mention_html(user: User | None, user_id: int) -> str:
    if user is not None and user.username:
        return f"@{user.username}"

    first = (user.first_name if user else "") or ""
    last = (user.last_name if user else "") or ""
    full_name = " ".join(x.strip() for x in (first, last) if x and x.strip()).strip() or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{full_name}</a>'


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

    user_ids = [int(r.user_id) for r in rows]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()}

    acked_user_ids = set(
        int(uid)
        for uid in db.scalars(
            select(CommandMessage.user_id).where(
                CommandMessage.chat_id == sess.chat_id,
                CommandMessage.command == REMINDER22_ACK_COMMAND,
                CommandMessage.session_date == sess.session_date,
            )
        ).all()
    )

    lines = ["⏰ А вот и 22:00. Ну что ребята, покакали?"]
    for st in rows:
        uid = int(st.user_id)
        u = users.get(uid)
        status = "✅" if uid in acked_user_ids else "❓"
        poops = int(st.poops_n or 0)
        who = _user_mention_html(u, uid)
        lines.append(f"{who} {status} 💩({poops})")

    return "\n".join(lines)


def build_late_reminder_text(db: Session, session_id: int) -> str | None:
    sess = db.get(DaySession, session_id)
    if sess is None:
        return None

    members = db.scalars(
        select(ChatMember).where(ChatMember.chat_id == sess.chat_id).order_by(ChatMember.joined_at.asc())
    ).all()
    if not members:
        return None

    member_ids = [int(m.user_id) for m in members]
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(member_ids))).all()}
    states = {
        int(s.user_id): s
        for s in db.scalars(
            select(SessionUserState).where(
                SessionUserState.session_id == session_id,
                SessionUserState.user_id.in_(member_ids),
            )
        ).all()
    }

    debtors: list[int] = []
    for uid in member_ids:
        st = states.get(uid)
        if st is None or int(st.poops_n or 0) <= 0:
            debtors.append(uid)

    if not debtors:
        return None

    lines = ["⏳ До закрытия сессии осталось немного времени. Кто ещё не отметился:"]
    for uid in debtors:
        lines.append(_user_mention_html(users.get(uid), uid))

    return "\n".join(lines)
