from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import SessionUserState


def set_bristol(db: Session, session_id: int, user_id: int, value: int) -> bool:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None or st.poops_n <= 0:
        return False
    st.bristol = value
    return True
