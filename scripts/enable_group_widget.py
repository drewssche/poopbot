"""Activate the single approved pilot and replace only today's tracked Q1."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from app.core.config import load_settings
from app.db.models import Chat, Session as DaySession, SessionMessage, WeeklyReport
from app.db.runtime import configure_database, close_database
from app.db.session import db_session
from app.services.app_setting_service import set_setting
from app.services.group_daily_widget import PILOT, FLAG, PREFIX, send
from app.services.time_service import get_session_window


async def main():
    cfg = load_settings()
    factory = configure_database(cfg)
    try:
        async with Bot(cfg.bot_token) as bot:
            group = await bot.get_chat(PILOT)
            if group.type not in ("group", "supergroup"):
                raise RuntimeError("Expected a group")
            new = None
            committed = False
            try:
                with db_session(factory) as db:
                    chat = db.get(Chat, PILOT, with_for_update=True)
                    window = get_session_window(chat.timezone)
                    sess = db.scalar(
                        select(DaySession)
                        .where(
                            DaySession.chat_id == PILOT,
                            DaySession.session_date == window.session_date,
                        )
                        .with_for_update()
                    )
                    if not sess or sess.status != "active" or window.is_blocked_window:
                        raise RuntimeError("No active session")
                    mapping = db.get(
                        SessionMessage, (sess.session_id, "Q1"), with_for_update=True
                    )
                    if not mapping:
                        raise RuntimeError("No existing daily question")
                    old = mapping.message_id
                    row = db.scalar(
                        select(WeeklyReport)
                        .where(
                            WeeklyReport.q1_session_id == sess.session_id,
                            WeeklyReport.photo_message_id == old,
                            WeeklyReport.disabled.is_(False),
                        )
                        .with_for_update()
                    )
                    if row and row.delivery_key.startswith(PREFIX):
                        print("Already active:", old)
                        return
                    set_setting(db, FLAG, str(PILOT))
                    if row:
                        row.disabled = True
                    new = await send(bot, db, sess)
                    if new.chat.id != PILOT or not getattr(new, 'widget', None):
                        raise RuntimeError('Telegram did not confirm the native group widget')
                    mapping.message_id = new.message_id
                committed = True
            finally:
                if new and not committed:
                    await bot.delete_message(chat_id=PILOT, message_id=new.message_id)
            print(
                "Active group widget:",
                new.message_id,
                "previous question:",
                old,
                flush=True,
            )
            try:
                await bot.delete_message(chat_id=PILOT, message_id=old)
            except TelegramBadRequest as exc:
                if "not found" not in exc.message.lower():
                    await bot.edit_message_reply_markup(
                        chat_id=PILOT, message_id=old, reply_markup=None
                    )
                    print("Previous question retained without controls")
    finally:
        close_database()


if __name__ == "__main__":
    asyncio.run(main())
