from __future__ import annotations

from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.models import CommandMessage


def get_command_message_id(db: Session, chat_id: int, user_id: int, command: str, session_date: date) -> int | None:
    row = db.get(
        CommandMessage,
        {"chat_id": chat_id, "user_id": user_id, "command": command, "session_date": session_date},
    )
    return row.message_id if row else None


def get_any_command_message_id(db: Session, chat_id: int, command: str, session_date: date) -> int | None:
    row = db.execute(
        select(CommandMessage.message_id)
        .where(
            CommandMessage.chat_id == chat_id,
            CommandMessage.command == command,
            CommandMessage.session_date == session_date,
        )
        .limit(1)
    ).first()
    return int(row.message_id) if row else None


def set_command_message_id(db: Session, chat_id: int, user_id: int, command: str, session_date: date, message_id: int) -> None:
    row = db.get(
        CommandMessage,
        {"chat_id": chat_id, "user_id": user_id, "command": command, "session_date": session_date},
    )
    if row is None:
        db.add(
            CommandMessage(
                chat_id=chat_id,
                user_id=user_id,
                command=command,
                session_date=session_date,
                message_id=message_id,
            )
        )
    else:
        row.message_id = message_id
