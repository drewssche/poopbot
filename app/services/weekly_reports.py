"""Durable weekly-card delivery and refresh, woken by committed PostgreSQL changes."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
from uuid import uuid4

import psycopg
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaPhoto
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from app.db.models import WeeklyReport
from app.services.command_message_service import set_command_message_id
from app.services.mini_app_notifications import database_work
from app.services.stats_common import period_to_range
from app.services.weekly_report_card import project_card, render_card

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_weekly_report(db, chat_id, anchor, *, destination=None, label=None, scheduled=False, chat_title=None):
    period = period_to_range(anchor, 'week')
    key = f'periodic:{chat_id}:{period.end}' if scheduled else f'debug:{uuid4()}'
    existing = db.scalar(select(WeeklyReport).where(WeeklyReport.delivery_key == key))
    if existing:
        return existing
    row = WeeklyReport(delivery_key=key, source_chat_id=chat_id,
                       destination_chat_id=destination if destination is not None else chat_id,
                       period_start=period.start, period_end=period.end, anchor_date=anchor,
                       chat_title=chat_title, label=label)
    db.add(row)
    db.flush()
    if scheduled:
        # The durable outbox owns delivery from now on. Zero is a pending marker,
        # replaced with the actual text message ID once delivery succeeds.
        set_command_message_id(db, chat_id, 0, 'weekly_stats', period.end, 0)
    return row


def split_report(text, limit=3500):
    """Keep report HTML links intact: its formatting is confined to individual lines."""
    chunks, current = [], ''
    for line in text.splitlines(keepends=True):
        if len(line.encode('utf-16-le')) // 2 > limit:
            raise ValueError('Report line exceeds Telegram limit')
        if current and len((current + line).encode('utf-16-le')) // 2 > limit:
            chunks.append(current.rstrip())
            current = ''
        current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def digest(value):
    return sha256(value.encode() if isinstance(value, str) else value).hexdigest()


class WeeklyReportWorker:
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
        from app.services.scheduler_reports import build_periodic_report_text
        with self.factory() as db:
            if db.get_bind().dialect.name == 'postgresql':
                db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            row = db.get(WeeklyReport, report_id)
            if row is None or row.disabled or row.revision <= row.delivered_revision:
                return None
            card = project_card(db, row.source_chat_id, row.period_start, row.period_end,
                                row.chat_title or 'Личные отметки', row.label)
            text = build_periodic_report_text(db, row.source_chat_id, row.anchor_date, 'week', '📉 Итоги недели')
            if row.label:
                text = f'{row.label}\n\n{text}'
            # Both projections see one database snapshot; rendering runs off-loop.
            chunks = split_report(text)
        return row, render_card(card), chunks

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
            row, picture, chunks = snapshot
            revision = row.revision
            picture_hash = digest(picture)
            if not row.photo_message_id:
                sent = await self.bot.send_photo(chat_id=row.destination_chat_id,
                    photo=BufferedInputFile(picture, 'weekly-report.png'), request_timeout=20)
                row.photo_message_id = sent.message_id
                await database_work(self.save_values, report_id,
                                    {'photo_message_id': sent.message_id, 'card_hash': picture_hash})
            elif row.card_hash != picture_hash:
                await self._edit(self.bot.edit_message_media, chat_id=row.destination_chat_id,
                    message_id=row.photo_message_id,
                    media=InputMediaPhoto(media=BufferedInputFile(picture, 'weekly-report.png')))
                await database_work(self.save_values, report_id, {'card_hash': picture_hash})
            messages = json.loads(row.text_messages)
            from app.bot.keyboards.debug import debug_explain_kb
            keyboard = debug_explain_kb('week') if row.delivery_key.startswith('debug:') else None
            for n, chunk in enumerate(chunks):
                text_hash = digest(chunk)
                if n >= len(messages):
                    sent = await self.bot.send_message(chat_id=row.destination_chat_id, text=chunk,
                                                      parse_mode='HTML', reply_markup=keyboard, request_timeout=20)
                    messages.append({'id': sent.message_id, 'hash': text_hash})
                elif messages[n]['hash'] != text_hash:
                    await self._edit(self.bot.edit_message_text, chat_id=row.destination_chat_id,
                                     message_id=messages[n]['id'], text=chunk, parse_mode='HTML', reply_markup=keyboard)
                    messages[n]['hash'] = text_hash
                else:
                    continue
                await database_work(self.save_values, report_id, {'text_messages': json.dumps(messages)})
            while len(messages) > len(chunks):
                try:
                    await self.bot.delete_message(chat_id=row.destination_chat_id,
                                                  message_id=messages[-1]['id'], request_timeout=20)
                except TelegramBadRequest as exc:
                    if 'message to delete not found' not in str(exc).lower():
                        raise
                messages.pop()
                await database_work(self.save_values, report_id, {'text_messages': json.dumps(messages)})
            await database_work(self.finish, row, messages)
            log.info('Weekly report delivered id=%s revision=%s', report_id, revision)
        except Exception as exc:
            # Never log full API exceptions: they can include private report content.
            log.warning('Weekly report failed id=%s error=%s', report_id, type(exc).__name__)
            notify = await database_work(self.failed, report_id, revision, exc)
            if notify and self.owner_id:
                try:
                    await self.bot.send_message(chat_id=self.owner_id,
                        text=f'⚠️ Не удалось обновить недельный отчёт №{report_id}. '
                             'Задание сохранено. Требуется проверка журнала бота.', request_timeout=15)
                except Exception:
                    log.warning('Could not notify owner about weekly report id=%s', report_id)

    def save_values(self, report_id, values):
        self.save(report_id, **values)

    def finish(self, snapshot, messages):
        with self.factory.begin() as db:
            # Do not consume revisions committed while Telegram was being updated.
            db.execute(update(WeeklyReport).where(WeeklyReport.id == snapshot.id).values(
                delivered_revision=snapshot.revision, attempts=0, retry_at=None, last_error=None))
            if snapshot.delivery_key.startswith('periodic:') and messages:
                set_command_message_id(db, snapshot.source_chat_id, 0, 'weekly_stats',
                                       snapshot.period_end, messages[0]['id'])

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
                    log.info('Weekly report listener ready')
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
                log.warning('Weekly report listener reconnecting: %s', type(exc).__name__)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(10)
