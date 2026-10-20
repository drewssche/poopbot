"""Durable calendar-report delivery and refresh, woken by committed PostgreSQL changes."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
from uuid import uuid4

import psycopg
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaPhoto, InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from app.db.models import WeeklyReport, Chat, Session as DaySession
from app.services.command_message_service import set_command_message_id
from app.services.mini_app_notifications import database_work
from app.services.stats_common import period_to_range, previous_period_range
from app.services.report_periods import report_period
from app.services.weekly_report_card import project_card
from app.services.report_image_cache import visual_fingerprint, shared_image
from app.services.weekly_report_caption import build_caption

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_report(db, chat_id, anchor, *, period='week', destination=None, label=None,
                   scheduled=False, chat_title=None):
    spec = report_period(period)
    current = period_to_range(anchor, period)
    previous = previous_period_range(anchor, period)
    # Keep legacy weekly keys so a restart/upgrade cannot publish them twice.
    prefix = 'periodic' if period == 'week' else f'periodic:{period}'
    key = f'{prefix}:{chat_id}:{current.end}' if scheduled else f'debug:{uuid4()}'
    existing = db.scalar(select(WeeklyReport).where(WeeklyReport.delivery_key == key))
    if existing:
        return existing
    row = WeeklyReport(delivery_key=key, source_chat_id=chat_id,
                       destination_chat_id=destination if destination is not None else chat_id,
                       report_period=period, previous_period_start=previous.start,
                       period_start=current.start, period_end=current.end, anchor_date=anchor,
                       chat_title=chat_title, label=label)
    db.add(row)
    db.flush()
    if scheduled:
        # The durable outbox owns delivery; replace the pending marker after sending.
        set_command_message_id(db, chat_id, 0, spec.command, current.end, 0)
    return row


def enqueue_weekly_report(db, chat_id, anchor, **kwargs):
    return enqueue_report(db, chat_id, anchor, period='week', **kwargs)



def digest(value):
    return sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class ReportWorker:
    def __init__(self, bot, factory, database_url, owner_id=None):
        self.bot, self.factory, self.database_url, self.owner_id = bot, factory, database_url, owner_id
        self.wake = asyncio.Event()

    def pending(self):
        with self.factory() as db:
            rows = db.execute(select(WeeklyReport.id, WeeklyReport.retry_at).where(
                WeeklyReport.disabled.is_(False), WeeklyReport.revision > WeeklyReport.delivered_revision,
                WeeklyReport.attempts < MAX_ATTEMPTS).order_by(WeeklyReport.retry_at.asc().nullsfirst(), WeeklyReport.id)
                .limit(32)).all()
        now = utcnow()
        ready = [row.id for row in rows if not row.retry_at or row.retry_at <= now]
        delays = [(row.retry_at-now).total_seconds() for row in rows if row.retry_at and row.retry_at > now]
        return ready, min(delays) if delays else None

    def get(self, report_id):
        with self.factory() as db:
            return db.get(WeeklyReport, report_id)

    def save(self, report_id, **values):
        with self.factory.begin() as db:
            db.execute(update(WeeklyReport).where(WeeklyReport.id == report_id).values(**values))

    def snapshot(self, report_id):
        with self.factory() as db:
            if db.get_bind().dialect.name == 'postgresql':
                db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            row = db.get(WeeklyReport, report_id)
            if row is None or row.disabled or row.revision <= row.delivered_revision:
                return None
            card = project_card(db, row.source_chat_id, row.period_start, row.period_end,
                                row.chat_title or 'Личные отметки', row.label, period=row.report_period)
            if row.q1_session_id is not None:
                from app.services.mini_app_message import session_question
                from app.services.q1_card_state import question_caption
                from app.services.time_service import now_in_tz
                sess = db.get(DaySession, row.q1_session_id)
                chat = db.get(Chat, row.source_chat_id)
                if row.q1_flow_text is not None and sess.status != 'closed':
                    caption = row.q1_flow_text
                    row._q1_markup = json.loads(row.q1_flow_markup)
                else:
                    payload = session_question(db, chat, sess, row.source_chat_id, now_in_tz(chat.timezone).date())
                    caption = payload['text']
                    row._q1_markup = payload['reply_markup']
                caption = question_caption(caption)
            else:
                caption = build_caption(db, row.source_chat_id, card)
            # Both projections see one database snapshot; rendering runs off-loop.
        row._visual_hash = visual_fingerprint(card)
        # The durable fingerprint avoids rendering unchanged visuals even after
        # a restart or cache eviction. Never skip media for a new publication.
        unchanged = row.photo_message_id and row.card_hash and row.visual_hash == row._visual_hash
        picture = None if unchanged else shared_image(row.source_chat_id, card, row._visual_hash)
        return row, picture, caption

    async def _edit(self, method, **kwargs):
        try:
            return await method(**kwargs, request_timeout=20)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise

    async def deliver(self, report_id):
        row = await database_work(self.get, report_id)
        if row is None or row.disabled:
            return
        revision = row.revision
        try:
            if not row.chat_title:
                if row.source_chat_id > 0:
                    title = 'Личные отметки'
                else:
                    chat = await self.bot.get_chat(row.source_chat_id, request_timeout=15)
                    title = chat.title or 'Групповой чат'
                await database_work(self.save_values, report_id, {'chat_title': title[:256]})
            snapshot = await database_work(self.snapshot, report_id)
            if snapshot is None:
                return
            row, picture, caption = snapshot
            revision = row.revision
            picture_hash = digest(picture) if picture is not None else row.card_hash
            caption_hash = digest(caption)
            from app.bot.keyboards.debug import debug_explain_kb
            keyboard = debug_explain_kb(row.report_period) if row.delivery_key.startswith('debug:') else None
            parse_mode = None
            if row.q1_session_id is not None:
                keyboard = InlineKeyboardMarkup(**row._q1_markup) if row._q1_markup else None
                caption_hash = digest(json.dumps([caption, row._q1_markup], ensure_ascii=False, sort_keys=True))
                parse_mode = 'HTML'
            if not row.photo_message_id:
                sent = await self.bot.send_photo(chat_id=row.destination_chat_id,
                    photo=BufferedInputFile(picture, f'{row.report_period}-report.png'), caption=caption,
                    parse_mode=parse_mode, reply_markup=keyboard, request_timeout=20)
                row.photo_message_id = sent.message_id
            elif row.card_hash != picture_hash:
                await self._edit(self.bot.edit_message_media, chat_id=row.destination_chat_id,
                    message_id=row.photo_message_id, reply_markup=keyboard,
                    media=InputMediaPhoto(media=BufferedInputFile(picture, f'{row.report_period}-report.png'),
                                          caption=caption, parse_mode=parse_mode))
            elif row.caption_hash != caption_hash:
                await self._edit(self.bot.edit_message_caption, chat_id=row.destination_chat_id,
                    message_id=row.photo_message_id, caption=caption, parse_mode=parse_mode, reply_markup=keyboard)
            await database_work(self.save_values, report_id,
                {'photo_message_id': row.photo_message_id, 'card_hash': picture_hash,
                 'visual_hash': row._visual_hash, 'caption_hash': caption_hash})
            # Conversion of reports published before captions were introduced.
            # IDs are recorded by the old publisher; never infer a message to delete.
            messages = json.loads(row.text_messages)
            while messages:
                message_id = messages[0]['id']
                try:
                    await self.bot.delete_message(chat_id=row.destination_chat_id, message_id=message_id, request_timeout=20)
                except TelegramBadRequest as exc:
                    if "message can't be deleted" in str(exc).lower():
                        await self._edit(self.bot.edit_message_text, chat_id=row.destination_chat_id,
                            message_id=message_id, text='Отчёт объединён с карточкой выше.', parse_mode=None)
                    elif 'message to delete not found' not in str(exc).lower():
                        raise
                messages.pop(0)
                await database_work(self.save_values, report_id, {'text_messages': json.dumps(messages)})
            await database_work(self.finish, row)
            log.info('Calendar report delivered id=%s revision=%s', report_id, revision)
        except Exception as exc:
            # Never log full API exceptions: they can include private report content.
            log.warning('Calendar report failed id=%s error=%s', report_id, type(exc).__name__)
            notify = await database_work(self.failed, report_id, revision, exc)
            if notify and self.owner_id:
                try:
                    await self.bot.send_message(chat_id=self.owner_id,
                        text=f'⚠️ Не удалось обновить отчёт №{report_id}. '
                             'Задание сохранено. Требуется проверка журнала бота.', request_timeout=15)
                except Exception:
                    log.warning('Could not notify owner about weekly report id=%s', report_id)

    def save_values(self, report_id, values):
        self.save(report_id, **values)

    def finish(self, snapshot):
        with self.factory.begin() as db:
            # Do not consume revisions committed while Telegram was being updated.
            db.execute(update(WeeklyReport).where(WeeklyReport.id == snapshot.id).values(
                delivered_revision=snapshot.revision, attempts=0, retry_at=None, last_error=None))
            if snapshot.delivery_key.startswith('periodic:'):
                set_command_message_id(db, snapshot.source_chat_id, 0, report_period(snapshot.report_period).command,
                                       snapshot.period_end, snapshot.photo_message_id)

    def failed(self, report_id, revision, exc):
        permanent = isinstance(exc, TelegramForbiddenError) or (
            isinstance(exc, TelegramBadRequest) and any(part in str(exc).lower() for part in
                ('message to edit not found', "message can't be edited", 'chat not found')))
        with self.factory.begin() as db:
            row = db.get(WeeklyReport, report_id, with_for_update=True)
            if row is None or row.revision != revision:
                return False  # New edits already queued a fresh attempt.
            row.attempts += 1
            delay = max(5, exc.retry_after) if isinstance(exc, TelegramRetryAfter) else min(300, 5 * 2 ** (row.attempts-1))
            row.retry_at = utcnow() + timedelta(seconds=delay)
            row.disabled = permanent
            row.last_error = type(exc).__name__
            return permanent or row.attempts == MAX_ATTEMPTS

    async def drain(self):
        self.wake.set()  # Recover pending deliveries on every startup/reconnect.
        delay = None
        while True:
            if delay is None:
                await self.wake.wait()
            else:
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=max(0, delay))
                except asyncio.TimeoutError:
                    pass
            self.wake.clear()
            await asyncio.sleep(.75)  # Coalesce Q1/Q2/Q3 and rapid Mini App edits.
            ready, delay = await database_work(self.pending)
            for report_id in ready:
                await self.deliver(report_id)
            if ready:
                self.wake.set()  # Drain batches / edits committed during delivery.

    async def run(self):
        url = make_url(self.database_url)
        params = dict(host=url.host, port=url.port or 5432, user=url.username, password=url.password,
                      dbname=url.database, **dict(url.query), autocommit=True, connect_timeout=10,
                      application_name='weekly_report_listener', keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
        while True:
            tasks = []
            try:
                async with await psycopg.AsyncConnection.connect(**params) as conn:
                    # One publisher across overlapping bot processes. Connection loss
                    # releases the lock and stops delivery until LISTEN is restored.
                    cursor = await conn.execute('SELECT pg_try_advisory_lock(73105422)')
                    if not (await cursor.fetchone())[0]:
                        await asyncio.sleep(30)
                        continue
                    await conn.execute('LISTEN weekly_reports_changed')
                    async def listen():
                        async for _ in conn.notifies():
                            self.wake.set()
                    tasks = [asyncio.create_task(listen()), asyncio.create_task(self.drain())]
                    log.info('Calendar report listener ready')
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        # Stop the publisher before releasing its advisory lock.
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Calendar report listener reconnecting: %s', type(exc).__name__)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(10)


# Existing operator scripts keep working while all periods share one worker.
WeeklyReportWorker = ReportWorker
