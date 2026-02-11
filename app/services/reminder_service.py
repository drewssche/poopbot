from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionUserState, User

REMINDER22_COMMAND = "reminder22"


def build_reminder_22_text(db: Session, session_id: int) -> str | None:
    rows = db.scalars(
        select(SessionUserState)
        .where(SessionUserState.session_id == session_id, SessionUserState.remind_22 == True)  # noqa: E712
        .order_by(SessionUserState.user_id.asc())
    ).all()
    if not rows:
        return None

    user_ids = [r.user_id for r in rows]
    users = {
        u.user_id: u
        for u in db.scalars(select(User).where(User.user_id.in_(user_ids))).all()
    }

    lines = ["⏰ А вот и 22:00. Ну что ребята, покакали?"]
    for st in rows:
        u = users.get(st.user_id)
        status = "✅" if (st.poops_n or 0) > 0 else "❓"
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

