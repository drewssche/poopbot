from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.db.models import RateLimit


def check_rate_limit(
    db: Session,
    chat_id: int,
    user_id: int,
    scope: str,
    cooldown_seconds: int = 2,
) -> bool:
    """
    True  => allowed
    False => blocked
    """
    now = datetime.utcnow()
    rl = db.get(RateLimit, {"chat_id": chat_id, "user_id": user_id, "scope": scope})

    if rl is None:
        rl = RateLimit(chat_id=chat_id, user_id=user_id, scope=scope, last_action_at=now)
        db.add(rl)
        return True

    if now - rl.last_action_at < timedelta(seconds=cooldown_seconds):
        return False

    rl.last_action_at = now
    return True
