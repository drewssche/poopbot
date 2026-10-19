"""One event-driven worker; DB receipts survive restart, idle workers do not poll."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.db.models import MiniAppRequest

logger = logging.getLogger(__name__)


def now_utc():
    return datetime.now(UTC).replace(tzinfo=None)


async def database_work(function, *args):
    # Cancellation cannot stop a thread. Keep shutdown from disposing its engine early.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def deliver_messages(gateway, payloads):
    failed = False
    for payload in payloads:
        try:
            await gateway.call('editMessageText', payload)
        except Exception:
            failed = True
    if failed:
        # Still refresh the remaining messages; the durable job retries failures
        # using fresh data, and already updated messages are harmless no-ops.
        raise RuntimeError('Session message refresh incomplete')


class NotificationWorker:
    def __init__(self, engine, notify):
        self.engine, self.notify = engine, notify
        self.wake = asyncio.Event()
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.run())
        self.wake.set()  # Drain durable jobs left by a previous process.

    def kick(self):
        self.wake.set()

    def batch(self):
        with Session(self.engine) as db:
            rows = db.scalars(select(MiniAppRequest).where(MiniAppRequest.notify_pending.is_(True))
                              .order_by(MiniAppRequest.notify_after.asc().nullsfirst(), MiniAppRequest.created_at)
                              .limit(32)).all()
            now = now_utc()
            jobs = {}
            next_delay = None
            for row in rows:
                if row.notify_after and row.notify_after > now:
                    delay = (row.notify_after - now).total_seconds()
                    next_delay = delay if next_delay is None else min(delay, next_delay)
                    continue
                key = (row.chat_id, row.user_id, row.notify_date)
                jobs.setdefault(key, []).append((row.user_id, row.request_id, row.notify_attempts))
            return jobs, next_delay

    def finish(self, receipts, success):
        with Session(self.engine) as db:
            for user, request_id, attempts in receipts:
                attempts += 1
                retry = not success and attempts < 5
                if not success and not retry:
                    logger.error('Mini App message refresh exhausted retries; saved records are intact')
                db.execute(update(MiniAppRequest).where(
                    MiniAppRequest.user_id == user, MiniAppRequest.request_id == request_id
                ).values(notify_pending=retry, notify_attempts=attempts,
                         notify_after=now_utc() + timedelta(seconds=min(300, 5 * 2**(attempts-1))) if retry else None))
            db.commit()

    async def run(self):
        delay = None
        while True:
            try:
                if delay is None:
                    await self.wake.wait()
                elif delay > 0:
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                self.wake.clear()
                jobs, delay = await database_work(self.batch)
                for (chat, user, day), receipts in jobs.items():
                    success = False
                    try:
                        await self.notify(chat, user, day.isoformat() if day else None)
                        success = True
                    except Exception as exc:
                        logger.warning('Mini App message refresh failed (%s), attempt %s',
                                       type(exc).__name__, max(r[2] for r in receipts) + 1)
                    await database_work(self.finish, receipts, success)
                if jobs:
                    delay = 0  # Drain remaining jobs; then sleep indefinitely when empty.
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('Mini App notification worker failed: %s', type(exc).__name__)
                delay = 30  # Bounded retry on DB outage, no tight loop.

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
