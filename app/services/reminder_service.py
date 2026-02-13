from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMember, Session as DaySession, SessionUserState, User

REMINDER22_COMMAND = "reminder22"
LATE_REMINDER_COMMAND = "late_reminder"


def _user_mention_html(user: User | None, user_id: int) -> str:
    if user is not None and user.username:
        return f"@{user.username}"

    first = (user.first_name if user else "") or ""
    last = (user.last_name if user else "") or ""
    full_name = " ".join(x.strip() for x in (first, last) if x and x.strip()).strip() or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{full_name}</a>'


def _collect_debtors(db: Session, session_id: int) -> list[tuple[int, User | None]]:
    sess = db.get(DaySession, session_id)
    if sess is None:
        return []

    members = db.scalars(
        select(ChatMember).where(ChatMember.chat_id == sess.chat_id).order_by(ChatMember.joined_at.asc())
    ).all()
    if not members:
        return []

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

    debtors: list[tuple[int, User | None]] = []
    for uid in member_ids:
        st = states.get(uid)
        if st is None or int(st.poops_n or 0) <= 0:
            debtors.append((uid, users.get(uid)))
    return debtors


def build_reminder_22_text(db: Session, session_id: int) -> str | None:
    debtors = _collect_debtors(db, session_id)
    if not debtors:
        return None

    lines = ["⏰ А вот и 22:00. Ну что ребята, покакали?"]
    for uid, user in debtors:
        lines.append(_user_mention_html(user, uid))
    return "\n".join(lines)


def build_late_reminder_text(db: Session, session_id: int) -> str | None:
    debtors = _collect_debtors(db, session_id)
    if not debtors:
        return None

    lines = ["⏳ До закрытия сессии осталось немного времени. Кто ещё не отметился:"]
    for uid, user in debtors:
        lines.append(_user_mention_html(user, uid))
    return "\n".join(lines)
