from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.bot.keyboards.streak_restore import streak_restore_keyboard
from app.db.models import Chat
from app.db.session import db_session
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.scheduler_telegram import safe_send_message

logger = logging.getLogger(__name__)

STREAK_RESTORE_INCIDENT_COMMAND = "streak_restore_incident"


def streak_restore_message_text(target_date: date) -> str:
    return (
        "⚠️ Из-за сбоя бот мог пропустить входящие сообщения.\n\n"
        f"Если у тебя сломался стрик из-за пропуска за {target_date.strftime('%d.%m.%Y')}, "
        "нажми кнопку ниже. Бот проверит, можно ли восстановить день именно тебе."
    )


def collect_streak_restore_incident_stats(db, *, target_date: date) -> dict[str, int]:
    group_rows = list(
        db.scalars(
            select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id < 0).order_by(Chat.chat_id.asc())
        ).all()
    )
    private_rows = list(
        db.scalars(
            select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id > 0).order_by(Chat.chat_id.asc())
        ).all()
    )
    return {
        "groups_sent": sum(
            1
            for chat_id in group_rows
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None
        ),
        "private_sent": sum(
            1
            for chat_id in private_rows
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None
        ),
    }


async def send_streak_restore_incident_messages(
    bot: Bot,
    session_factory: sessionmaker,
    *,
    target_date: date,
    scope: str,
    chat_throttle_sec: float = 0.2,
) -> dict[str, int]:
    if scope not in {"groups", "private"}:
        raise ValueError(f"Unsupported scope: {scope}")

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    with db_session(session_factory) as db:
        if scope == "groups":
            chat_ids = list(
                db.scalars(
                    select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id < 0).order_by(Chat.chat_id.asc())
                ).all()
            )
        else:
            chat_ids = list(
                db.scalars(
                    select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id > 0).order_by(Chat.chat_id.asc())
                ).all()
            )

    for chat_id in chat_ids:
        with db_session(session_factory) as db:
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None:
                skipped_count += 1
                continue
            try:
                sent = await safe_send_message(
                    bot,
                    chat_id=int(chat_id),
                    text=streak_restore_message_text(target_date),
                    reply_markup=streak_restore_keyboard(target_date.isoformat()),
                )
                set_command_message_id(
                    db,
                    int(chat_id),
                    0,
                    STREAK_RESTORE_INCIDENT_COMMAND,
                    target_date,
                    sent.message_id,
                )
                sent_count += 1
            except Exception:
                logger.exception("Failed to send streak restore incident message chat_id=%s scope=%s", chat_id, scope)
                failed_count += 1
        if chat_throttle_sec > 0:
            import asyncio

            await asyncio.sleep(chat_throttle_sec)

    return {"sent": sent_count, "skipped": skipped_count, "failed": failed_count}
