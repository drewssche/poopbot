from __future__ import annotations

import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.orm import Session
from app.bot.keyboards.q2 import q2_keyboard
from app.bot.keyboards.q3 import q3_keyboard
from app.services.repo_service import get_session_message_id, set_session_message_id
# Re-export shared renderers for existing bot handlers.
from app.services.q2_q3_render import (
    Q2_TEXT, Q3_TEXT, Q2_EMOJI, Q3_EMOJI, q2_choice_from_bristol,
    render_q2_text, render_q3_text, render_q2_private_text, render_q3_private_text,
    should_show_q2_q3_button,
)

logger = logging.getLogger(__name__)

async def ensure_q2_q3_exist(bot: Bot, db: Session, chat_id: int, session_id: int) -> None:
    q1_id = get_session_message_id(db, session_id, "Q1")
    if not q1_id:
        return

    q2_id = get_session_message_id(db, session_id, "Q2")
    q2_alive = False
    if q2_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=q2_id,
                text=render_q2_text(db, chat_id, session_id),
                reply_markup=q2_keyboard(),
            )
            q2_alive = True
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                q2_alive = True
            elif "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q2_alive = False
            else:
                logger.exception("Q2 edit check failed: %s", e)

    if not q2_alive:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=render_q2_text(db, chat_id, session_id),
            reply_markup=q2_keyboard(),
        )
        set_session_message_id(db, session_id, "Q2", sent.message_id)

    q3_id = get_session_message_id(db, session_id, "Q3")
    q3_alive = False
    if q3_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=q3_id,
                text=render_q3_text(db, chat_id, session_id),
                reply_markup=q3_keyboard(),
            )
            q3_alive = True
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                q3_alive = True
            elif "message to edit not found" in msg or "message not found" in msg or "message_id_invalid" in msg:
                q3_alive = False
            else:
                logger.exception("Q3 edit check failed: %s", e)

    if not q3_alive:
        await asyncio.sleep(1)
        sent = await bot.send_message(
            chat_id=chat_id,
            text=render_q3_text(db, chat_id, session_id),
            reply_markup=q3_keyboard(),
        )
        set_session_message_id(db, session_id, "Q3", sent.message_id)
