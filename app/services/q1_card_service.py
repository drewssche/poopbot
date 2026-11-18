"""Publish low daily calendar cards; keep legacy text questions editable."""
import asyncio
from hashlib import sha256
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile
from app.services.daily_widget_demo import widget_send_denied
from app.db.models import Chat, Session as DaySession, WeeklyReport
from app.services.repo_service import get_session_message_id, set_session_message_id
from app.services.q1_card_state import queue_question_view, question_caption
from app.services.scheduler_telegram import safe_edit_message_text
from app.services.stats_common import period_to_range
from app.services.weekly_report_card import project_card
from app.services.report_image_cache import visual_fingerprint, shared_image


async def edit_question(bot, db, *, chat_id, message_id, text, reply_markup=None, **kwargs):
    if queue_question_view(db, chat_id, message_id, text, reply_markup):
        return None
    return await safe_edit_message_text(bot, chat_id=chat_id, message_id=message_id,
                                       text=text, reply_markup=reply_markup, **kwargs)


async def send_question(bot, db, *, chat_id, session_id, text, reply_markup=None):
    sess = db.get(DaySession, session_id)
    from app.services import group_daily_widget
    if group_daily_widget.enabled(db, chat_id):
        try:
            with db.begin_nested():
                return await group_daily_widget.send(bot, db, sess)
        except TelegramBadRequest as exc:
            if not widget_send_denied(exc):
                raise
            # Roll back the unsent widget publisher before registering the photo.

    period = period_to_range(sess.session_date, 'week')
    title = 'Личные отметки' if chat_id > 0 else 'Отметки этого чата'
    db.flush()
    card = project_card(db, chat_id, period.start, period.end, title, layout='daily')
    fingerprint = await asyncio.to_thread(visual_fingerprint, card)
    picture = await asyncio.to_thread(shared_image, chat_id, card, fingerprint)
    caption = question_caption(text)
    sent = await bot.send_photo(chat_id=chat_id, photo=BufferedInputFile(picture, 'daily-week.png'),
                                caption=caption, parse_mode='HTML', reply_markup=reply_markup,
                                request_timeout=20)
    markup = reply_markup.model_dump(exclude_none=True) if hasattr(reply_markup, 'model_dump') else reply_markup
    db.add(WeeklyReport(
        delivery_key=f'q1:{session_id}:{uuid4()}', source_chat_id=chat_id, destination_chat_id=chat_id,
        q1_session_id=session_id, report_period='week', previous_period_start=period.start,
        period_start=period.start, period_end=period.end, anchor_date=sess.session_date,
        chat_title=title, label=None, photo_message_id=sent.message_id,
        card_hash=sha256(picture).hexdigest(),
        visual_hash=fingerprint,
        caption_hash=sha256(json.dumps([caption, markup], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        revision=1, delivered_revision=1))
    db.flush()
    return sent


def recover_question(db, sess, message, bot_id):
    """Recover a delivered Q1 whose send response or following commit was lost."""
    sender = getattr(message, "from_user", None)
    if (not sender or sender.id != bot_id or not sender.is_bot
            or getattr(message, "forward_origin", None)
            or not getattr(message, "photo", None)
            or message.chat.id != sess.chat_id):
        return False
    title = "💩 Твоя личная сессия" if sess.chat_id > 0 else "💩 Кто сегодня какал?"
    expected = f"{title} ({sess.session_date.strftime('%d.%m.%y')})"
    if (message.caption or "").split("\n", 1)[0] != expected:
        return False
    timezone = ZoneInfo(db.get(Chat, sess.chat_id).timezone)
    if message.date.astimezone(timezone).date() != sess.session_date:
        return False
    markup = message.reply_markup
    if not markup or not any(b.callback_data == "q1:plus" for row in markup.inline_keyboard for b in row):
        return False
    # Concurrent first taps may recover only the same canonical question.
    db.refresh(sess, with_for_update=True)
    if sess.status != "active":
        return False
    current = get_session_message_id(db, sess.session_id, "Q1")
    if current is not None:
        return current == message.message_id
    period = period_to_range(sess.session_date, "week")
    row = db.scalar(select(WeeklyReport).where(
        WeeklyReport.source_chat_id == sess.chat_id,
        WeeklyReport.q1_session_id == sess.session_id,
        WeeklyReport.photo_message_id == message.message_id,
        WeeklyReport.delivery_key.startswith("q1:"),
        WeeklyReport.disabled.is_(False),
    ))
    if row is None:
        db.add(WeeklyReport(
            delivery_key=f"q1:{sess.session_id}:recovered:{message.message_id}",
            source_chat_id=sess.chat_id, destination_chat_id=sess.chat_id,
            q1_session_id=sess.session_id, report_period="week",
            previous_period_start=period.start, period_start=period.start,
            period_end=period.end, anchor_date=sess.session_date,
            chat_title="Личные отметки" if sess.chat_id > 0 else "Отметки этого чата",
            photo_message_id=message.message_id, revision=1, delivered_revision=0,
        ))
    set_session_message_id(db, sess.session_id, "Q1", message.message_id)
    db.flush()
    return True
