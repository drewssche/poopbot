from __future__ import annotations

from datetime import datetime, time, date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chat, User, ChatMember, Session as DaySession, SessionMessage, SessionUserState, UserStreak


def upsert_chat(db: Session, chat_id: int) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id, timezone="Europe/Minsk", post_time=time(10, 0), is_enabled=True)
        db.add(chat)
    elif not chat.is_enabled:
        chat.is_enabled = True
    return chat


def upsert_user(db: Session, user_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, username=username, first_name=first_name, last_name=last_name)
        db.add(user)
        return user

    user.username = username
    user.first_name = first_name
    user.last_name = last_name
    return user


def ensure_chat_member(db: Session, chat_id: int, user_id: int) -> ChatMember:
    user = db.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, username=None, first_name=None, last_name=None)
        db.add(user)
        db.flush()

    member = db.get(ChatMember, {"chat_id": chat_id, "user_id": user_id})
    if member is None:
        member = ChatMember(chat_id=chat_id, user_id=user_id, joined_at=datetime.utcnow())
        db.add(member)

        # streak row (per chat+user) create too
        streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})
        if streak is None:
            db.add(UserStreak(chat_id=chat_id, user_id=user_id, current_streak=0, last_poop_date=None))
    return member


def get_or_create_session(db: Session, chat_id: int, session_date: date) -> DaySession:
    stmt = select(DaySession).where(DaySession.chat_id == chat_id, DaySession.session_date == session_date)
    sess = db.scalar(stmt)
    if sess is None:
        sess = DaySession(chat_id=chat_id, session_date=session_date, status="active", start_at=datetime.utcnow(), end_at=None)
        db.add(sess)
        db.flush()  # to get session_id
    return sess


def get_session_message_id(db: Session, session_id: int, kind: str) -> Optional[int]:
    sm = db.get(SessionMessage, {"session_id": session_id, "kind": kind})
    return sm.message_id if sm else None


def set_session_message_id(db: Session, session_id: int, kind: str, message_id: int) -> None:
    sm = db.get(SessionMessage, {"session_id": session_id, "kind": kind})
    if sm is None:
        sm = SessionMessage(session_id=session_id, kind=kind, message_id=message_id)
        db.add(sm)
    else:
        sm.message_id = message_id


def get_or_create_session_user_state(db: Session, session_id: int, user_id: int) -> SessionUserState:
    sus = db.get(SessionUserState, {"session_id": session_id, "user_id": user_id})
    if sus is None:
        sus = SessionUserState(
            session_id=session_id,
            user_id=user_id,
            poops_n=0,
            achievement_text=None,
            remind_22=False,
            bristol=None,
            feeling=None,
            updated_at=datetime.utcnow(),
        )
        db.add(sus)
    return sus


def list_chat_members(db: Session, chat_id: int) -> list[tuple[User, UserStreak]]:
    """
    Returns all members (users) of a chat (from chat_members), with streak rows.
    """
    stmt = (
        select(User, UserStreak)
        .join(ChatMember, ChatMember.user_id == User.user_id)
        .join(UserStreak, (UserStreak.user_id == User.user_id) & (UserStreak.chat_id == ChatMember.chat_id))
        .where(ChatMember.chat_id == chat_id)
    )
    return list(db.execute(stmt).all())
