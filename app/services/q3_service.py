from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import SessionUserState


def set_feeling(db: Session, session_id: int, user_id: int, value: str) -> bool:
    st = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if st is None or st.poops_n <= 0:
        return False
    st.feeling = value
    return True
