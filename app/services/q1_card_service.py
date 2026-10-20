"""Publish new daily questions with weekly media; keep legacy text questions editable."""
import asyncio
from hashlib import sha256
import json
from uuid import uuid4

from aiogram.types import BufferedInputFile
from sqlalchemy import select
from app.db.models import Session as DaySession, WeeklyReport
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
    period = period_to_range(sess.session_date, 'week')
    title = 'Личные отметки'
    if chat_id < 0:
        title = db.scalar(select(WeeklyReport.chat_title).where(
            WeeklyReport.source_chat_id == chat_id, WeeklyReport.chat_title.is_not(None))
            .order_by(WeeklyReport.id.desc()).limit(1))
        if not title:
            chat = await bot.get_chat(chat_id, request_timeout=15)
            title = chat.title or 'Групповой чат'
    db.flush()
    card = project_card(db, chat_id, period.start, period.end, title, 'Текущая неделя')
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
        chat_title=title, label='Текущая неделя', photo_message_id=sent.message_id,
        card_hash=sha256(picture).hexdigest(),
        visual_hash=fingerprint,
        caption_hash=sha256(json.dumps([caption, markup], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        revision=1, delivered_revision=1))
    db.flush()
    return sent
