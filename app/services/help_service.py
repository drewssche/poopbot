from __future__ import annotations

from datetime import time
from sqlalchemy.orm import Session
from sqlalchemy import delete, select

from app.db.models import Chat, ChatMember, CommandMessage, PoopEvent, Session as DaySession, SessionUserState, User, UserStreak


def set_chat_post_time(db: Session, chat_id: int, hour: int) -> None:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        db.add(chat)
    chat.post_time = time(hour, 0)


def set_chat_global_visibility(db: Session, chat_id: int, enabled: bool) -> None:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        db.add(chat)
    chat.show_in_global = bool(enabled)


def set_chat_notifications_enabled(db: Session, chat_id: int, enabled: bool) -> None:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        db.add(chat)
    chat.notifications_enabled = bool(enabled)


def set_help_message(db: Session, chat_id: int, message_id: int, owner_id: int) -> None:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        db.add(chat)
    chat.help_message_id = message_id
    chat.help_owner_id = owner_id


def get_help_message(db: Session, chat_id: int) -> tuple[int | None, int | None]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        return None, None
    return chat.help_message_id, chat.help_owner_id


def delete_user_everywhere(db: Session, chat_id: int, user_id: int) -> None:
    db.execute(delete(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id))
    db.execute(delete(UserStreak).where(UserStreak.chat_id == chat_id, UserStreak.user_id == user_id))
    db.execute(delete(SessionUserState).where(SessionUserState.user_id == user_id))
    db.execute(delete(User).where(User.user_id == user_id))


def delete_user_from_chat(db: Session, chat_id: int, user_id: int) -> None:
    chat_session_ids = select(DaySession.session_id).where(DaySession.chat_id == chat_id)

    db.execute(delete(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id))
    db.execute(delete(UserStreak).where(UserStreak.chat_id == chat_id, UserStreak.user_id == user_id))
    db.execute(
        delete(SessionUserState).where(
            SessionUserState.user_id == user_id,
            SessionUserState.session_id.in_(chat_session_ids),
        )
    )
    db.execute(
        delete(PoopEvent).where(
            PoopEvent.user_id == user_id,
            PoopEvent.session_id.in_(chat_session_ids),
        )
    )
    db.execute(delete(CommandMessage).where(CommandMessage.chat_id == chat_id, CommandMessage.user_id == user_id))
