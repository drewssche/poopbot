"""Opt-in integration checks against an empty, migrated disposable PostgreSQL DB.
Run with WEEKLY_REPORT_TEST_DATABASE_URL; never point it at an operational DB.
"""
import asyncio
from datetime import date, timedelta
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Chat, PoopEvent, Session as DaySession, User, WeeklyReport
from app.services.weekly_reports import WeeklyReportWorker, enqueue_weekly_report

URL = os.getenv('WEEKLY_REPORT_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='disposable PostgreSQL required')
DAY = date(2026, 10, 12)


@pytest.fixture
def pg():
    engine = create_engine(URL)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    # A hard guard prevents accidentally running cleanup on a populated project DB.
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM chats')) == 0
    with factory.begin() as db:
        db.add_all([Chat(chat_id=-11), Chat(chat_id=-12), User(user_id=11)])
        db.flush()
        session = DaySession(chat_id=-11, session_date=DAY)
        db.add(session); db.flush()
        sid = session.session_id
        report = enqueue_weekly_report(db, -11, DAY, chat_title='Тест', scheduled=True)
        rid = report.id
    yield engine, factory, sid, rid
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE chats, users CASCADE'))
    engine.dispose()


def add_mark(db, sid):
    row = PoopEvent(session_id=sid, user_id=11, origin_chat_id=-11, event_n=1)
    db.add(row); db.flush()
    return row.id


def test_daily_questions_only_refresh_their_chat_week_and_survive_rollback(pg):
    _, factory, sid, _ = pg
    from app.services.weekly_reports import enqueue_report
    with factory.begin() as db:
        tomorrow = DaySession(chat_id=-11, session_date=DAY+timedelta(days=1))
        next_week = DaySession(chat_id=-11, session_date=DAY+timedelta(days=7))
        foreign = DaySession(chat_id=-12, session_date=DAY)
        db.add_all([tomorrow, next_week, foreign]); db.flush()
        ids = []
        for sess in [db.get(DaySession, sid), tomorrow, next_week, foreign]:
            row = enqueue_report(db, sess.chat_id, sess.session_date, chat_title='Тест')
            row.q1_session_id = sess.session_id
            row.previous_period_start = row.period_start
            ids.append(row.id)
        tomorrow_id = tomorrow.session_id
    def revisions():
        with factory() as db:
            return [db.get(WeeklyReport, rid).revision for rid in ids]
    before = revisions()
    with factory() as db:
        add_mark(db, sid)
        db.rollback()
    assert revisions() == before
    with factory.begin() as db:
        eid = add_mark(db, sid)
    after = revisions()
    assert after[0] > before[0] and after[1] > before[1]
    assert after[2:] == before[2:]
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
    assert revisions()[0] > after[0]
    with factory.begin() as db:
        db.delete(db.get(DaySession, tomorrow_id))
    with factory() as db:
        assert db.get(WeeklyReport, ids[1]) is None


def test_committed_triggers_cover_insert_edit_bulk_delete_restore_and_session_delete(pg):
    _, factory, sid, rid = pg
    def rev():
        with factory() as db:
            return db.get(WeeklyReport, rid).revision
    old = rev()
    with factory.begin() as db:
        eid = add_mark(db, sid)
    assert rev() > old; old = rev()
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
    assert rev() > old; old = rev()
    with factory.begin() as db:
        db.execute(delete(PoopEvent).where(PoopEvent.id == eid))
    assert rev() > old; old = rev()
    with factory.begin() as db:
        add_mark(db, sid)
    assert rev() > old; old = rev()
    with factory.begin() as db:
        db.delete(db.get(DaySession, sid))
    assert rev() > old


def test_rollback_and_foreign_origin_do_not_dirty_reports(pg):
    _, factory, sid, rid = pg
    with factory() as db:
        add_mark(db, sid)
        db.rollback()
    with factory.begin() as db:
        db.add(PoopEvent(session_id=sid, user_id=11, origin_chat_id=-12, event_n=1))
    with factory() as db:
        assert db.get(WeeklyReport, rid).revision == 1


def test_previous_week_affects_trend_but_future_marks_do_not(pg):
    _, factory, _, rid = pg
    with factory.begin() as db:
        session = DaySession(chat_id=-12, session_date=DAY-timedelta(days=7))
        db.add(session); db.flush()
        db.add(PoopEvent(session_id=session.session_id, user_id=11, origin_chat_id=-12, event_n=1))
    with factory() as db:
        old = db.get(WeeklyReport, rid).revision
        assert old > 1  # Cross-chat comparison in the accompanying full report.
    with factory.begin() as db:
        session = DaySession(chat_id=-11, session_date=DAY+timedelta(days=7))
        db.add(session); db.flush()
        add_mark(db, session.session_id)
    with factory() as db:
        assert db.get(WeeklyReport, rid).revision == old


def test_listener_recovers_jobs_and_reacts_to_commits_without_idle_queries(pg):
    engine, factory, sid, rid = pg
    bot = SimpleNamespace(**{name: AsyncMock(return_value=SimpleNamespace(message_id=10 if name == 'send_photo' else 11))
        for name in ('send_photo', 'send_message', 'edit_message_media', 'edit_message_text', 'edit_message_caption', 'delete_message')})
    worker = WeeklyReportWorker(bot, factory, URL)
    async def wait_for(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                await asyncio.sleep(.05)
    async def scenario():
        task = asyncio.create_task(worker.run())
        try:
            await wait_for(lambda: worker.get(rid).delivered_revision == 1)
            with factory.begin() as db:
                add_mark(db, sid)
            await wait_for(lambda: worker.get(rid).delivered_revision == worker.get(rid).revision)
            bot.send_photo.assert_awaited_once()
            bot.send_message.assert_not_awaited()
            bot.edit_message_media.assert_awaited_once()
            await asyncio.sleep(1.1)  # Let the final drain finish.
            statements = []
            def capture(*args): statements.append(args[2])
            event.listen(engine, 'before_cursor_execute', capture)
            await asyncio.sleep(1.2)
            event.remove(engine, 'before_cursor_execute', capture)
            assert statements == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('period,anchor,previous_day,current_day,future_day', [
    ('month', date(2026, 3, 15), date(2026, 2, 1), date(2026, 3, 31), date(2026, 4, 1)),
    ('year', date(2024, 10, 1), date(2023, 1, 1), date(2024, 2, 29), date(2025, 1, 1)),
])
def test_calendar_invalidation_spans_previous_period_and_all_changes(pg, period, anchor, previous_day, current_day, future_day):
    from app.services.weekly_reports import enqueue_report
    _, factory, _, _ = pg
    with factory.begin() as db:
        row = enqueue_report(db, -11, anchor, period=period, scheduled=True, chat_title='Тест')
        rid = row.id
    def revision():
        with factory() as db:
            return db.get(WeeklyReport, rid).revision
    # This comparison date is much older than the old seven-day invalidation window.
    with factory.begin() as db:
        other = DaySession(chat_id=-12, session_date=previous_day)
        db.add(other); db.flush()
        db.add(PoopEvent(session_id=other.session_id, user_id=11, origin_chat_id=-12, event_n=1))
    assert revision() > 1
    old = revision()
    with factory.begin() as db:
        current = DaySession(chat_id=-11, session_date=current_day)
        db.add(current); db.flush(); sid = current.session_id
        eid = add_mark(db, sid)
    assert revision() > old; old = revision()
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
    assert revision() > old; old = revision()
    with factory.begin() as db:
        db.execute(delete(PoopEvent).where(PoopEvent.id == eid))
    assert revision() > old; old = revision()
    with factory.begin() as db:
        add_mark(db, sid)
    assert revision() > old; old = revision()
    with factory.begin() as db:
        db.delete(db.get(DaySession, sid))
    assert revision() > old; old = revision()
    with factory.begin() as db:
        future = DaySession(chat_id=-11, session_date=future_day)
        db.add(future); db.flush(); add_mark(db, future.session_id)
    assert revision() == old
