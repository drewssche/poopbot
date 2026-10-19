from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.core.config import load_settings
from app.db.runtime import get_session_factory
from app.db.models import Chat, Session as DaySession, SessionMessage
from app.db.session import db_session
from app.services.app_setting_service import RESTORE_CLAIM_ENABLED_KEY, get_bool_setting
from app.services.restore_period_service import (
    can_mark_closed_session, get_restore_period, lock_restore_chat, mark_forgotten_day, restore_period,
)
from app.services.repo_service import ensure_chat_member, upsert_user
from app.services.rate_limit_service import check_rate_limit
from app.services.scheduler_service import _lock_q1, _refresh_current_q1_view
from app.services.time_service import now_in_tz

logger = logging.getLogger(__name__)
router = Router()
_session_factory = None


def init_db(database_url: str) -> None:
    global _session_factory
    _session_factory = get_session_factory(database_url)


async def _answer(cb, message: str) -> None:
    try:
        await cb.answer(message, show_alert=True)
    except TelegramAPIError:
        logger.warning("Restore acknowledgement failed chat_id=%s user_id=%s action=%s",
                       cb.message.chat.id, cb.from_user.id, cb.data)


def _ensure_actor(db, cb):
    user = cb.from_user
    upsert_user(db, user_id=user.id, username=user.username, first_name=user.first_name, last_name=user.last_name)
    db.flush()
    ensure_chat_member(db, chat_id=cb.message.chat.id, user_id=user.id)
    db.flush()


@router.callback_query(F.data == "restore:claim")
async def restore_streak_claim(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return
    init_db(load_settings().database_url)
    committed = False
    try:
        with db_session(_session_factory) as db:
            chat = db.get(Chat, cb.message.chat.id)
            if chat is None:
                await _answer(cb, "Чат не найден")
                return
            if not get_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, default=False):
                await _answer(cb, "Восстановление сейчас отключено")
                return
            period = get_restore_period(db)
            if period is None:
                await _answer(cb, "Администратору нужно выбрать период в /streak")
                return
            today = now_in_tz(chat.timezone).date()
            lock_restore_chat(db, chat.chat_id)
            _ensure_actor(db, cb)
            if not check_rate_limit(db, chat_id=chat.chat_id, user_id=cb.from_user.id, scope="RESTORE_STREAK", cooldown_seconds=4):
                await _answer(cb, "Подожди пару секунд перед повторным нажатием")
                return
            changed, message = restore_period(db, chat_id=chat.chat_id, user_id=cb.from_user.id, period=period, today=today)
            db.commit()
            committed = True
            logger.info("Restore claim chat_id=%s user_id=%s period=%s changed=%s committed=true", chat.chat_id, cb.from_user.id, period.label, changed)
            await _answer(cb, message)
            if changed:
                await _refresh_current_q1_view(cb.bot, db, chat.chat_id, today)
    except Exception:
        logger.exception("Restore claim failed committed=%s", committed)
        if not committed:
            await _answer(cb, "Не удалось сохранить отметки, попробуй ещё раз")


@router.callback_query(F.data == "q1:forgot")
async def forgotten_day_claim(cb: CallbackQuery) -> None:
    if cb.message is None or cb.from_user is None:
        return
    init_db(load_settings().database_url)
    committed = False
    try:
        with db_session(_session_factory) as db:
            chat = db.get(Chat, cb.message.chat.id)
            if chat is None:
                await _answer(cb, "Чат не найден")
                return
            lock_restore_chat(db, chat.chat_id)
            sess = db.scalar(select(DaySession).join(SessionMessage).where(
                DaySession.chat_id == chat.chat_id, SessionMessage.kind == "Q1",
                SessionMessage.message_id == cb.message.message_id,
            ))
            if sess is None or sess.status != "closed":
                await _answer(cb, "Эта кнопка доступна только под закрытой сессией")
                return
            today = now_in_tz(chat.timezone).date()
            if not can_mark_closed_session(sess, today):
                logger.info("Forgotten mark rejected chat_id=%s user_id=%s session_id=%s reason=expired", chat.chat_id, cb.from_user.id, sess.session_id)
                await _answer(cb, "Срок истёк: отметиться можно до конца следующего дня")
                return
            _ensure_actor(db, cb)
            changed, message = mark_forgotten_day(db, sess=sess, user_id=cb.from_user.id, today=today)
            db.commit()
            committed = True
            logger.info("Forgotten mark chat_id=%s user_id=%s session_id=%s changed=%s committed=true", chat.chat_id, cb.from_user.id, sess.session_id, changed)
            await _answer(cb, message)
            # Also retry the view refresh on a repeated click after a network failure.
            await _lock_q1(cb.bot, db, chat.chat_id, sess.session_id)
            await _refresh_current_q1_view(cb.bot, db, chat.chat_id, today)
    except Exception:
        logger.exception("Forgotten mark failed committed=%s", committed)
        if not committed:
            await _answer(cb, "Не удалось сохранить отметку, попробуй ещё раз")
