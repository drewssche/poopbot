from __future__ import annotations

from datetime import time
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.db.models import Chat, ChatMember, UserStreak, SessionUserState, User


def set_chat_post_time(db: Session, chat_id: int, hour: int) -> None:
    chat = db.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        db.add(chat)
    chat.post_time = time(hour, 0)


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
