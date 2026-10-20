import asyncio
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Chat, CommandMessage, Session as DaySession, SessionMessage
from app.db.session import db_session
from app.services import scheduler_scan, scheduler_service as scheduler, time_service
from app.services.scheduler_reports import periodic_reports_due


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    clock = [datetime(2026, 10, 19, 10, tzinfo=timezone.utc)]

    def now(tz):
        # Match production's timezone validation as well as its local clock.
        import pytz
        return clock[0].astimezone(pytz.timezone(tz))

    for module in (scheduler_scan, scheduler, time_service):
        monkeypatch.setattr(module, "now_in_tz", now)
    scheduler._streak_recalc_date.clear()
    scheduler._q1_catchup_skip_date.clear()
    yield factory, engine, clock
    scheduler._streak_recalc_date.clear()
    scheduler._q1_catchup_skip_date.clear()
    engine.dispose()


def seed(factory, chat_id=-100, *, day=date(2026, 10, 19), q1=True,
         reports=True, enabled=True, notifications=True, status="active", tz="Europe/Minsk"):
    with db_session(factory) as db:
        db.add(Chat(chat_id=chat_id, timezone=tz, post_time=time(10),
                    is_enabled=enabled, notifications_enabled=notifications))
        session = DaySession(chat_id=chat_id, session_date=day, status=status)
        db.add(session)
        db.flush()
        if q1:
            db.add(SessionMessage(session_id=session.session_id, kind="Q1", message_id=1))
        if reports:
            for kind, _, _ in periodic_reports_due(day - timedelta(days=1)):
                db.add(CommandMessage(chat_id=chat_id, user_id=0, command=kind,
                                      session_date=day-timedelta(days=1), message_id=1))
        return session.session_id


def test_idle_fifty_chats_use_three_reads_and_no_writes(setup, monkeypatch):
    factory, engine, _ = setup
    for i in range(50):
        seed(factory, -100-i)
    sql = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, s, p, ctx, many: sql.append(s))
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, chat_throttle_sec=0))
    worker.assert_not_awaited()
    assert len(sql) == 3
    assert all(s.lstrip().upper().startswith("SELECT") for s in sql)


def test_settings_and_new_chat_are_reread_next_tick(setup, monkeypatch):
    factory, _, _ = setup
    seed(factory, q1=False, notifications=False)
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, 0))
    worker.assert_not_awaited()
    with db_session(factory) as db:
        db.get(Chat, -100).notifications_enabled = True
        db.add(Chat(chat_id=-200))
    asyncio.run(scheduler._tick(None, factory, 0))
    assert {c.args[2] for c in worker.await_args_list} == {-100, -200}


def test_missing_q1_is_posted_once_and_rechecked_after_snapshot(setup, monkeypatch):
    factory, _, _ = setup
    session_id = seed(factory, q1=False)

    async def post(bot, db, chat_id, session_id, *args, **kwargs):
        db.add(SessionMessage(session_id=session_id, kind="Q1", message_id=123))

    post_mock = AsyncMock(side_effect=post)
    monkeypatch.setattr(scheduler, "_post_q1", post_mock)
    asyncio.run(scheduler._tick(None, factory, 0))
    asyncio.run(scheduler._tick(None, factory, 0))
    assert post_mock.await_count == 1

    # A concurrent command may publish after the scan. The actual worker must
    # see its marker, not send using the stale snapshot.
    with db_session(factory) as db:
        db.delete(db.get(SessionMessage, (session_id, "Q1")))
    original = scheduler.load_scheduler_snapshot

    def concurrent_publish(db):
        snapshot = original(db)
        db.add(SessionMessage(session_id=session_id, kind="Q1", message_id=456))
        return snapshot

    monkeypatch.setattr(scheduler, "load_scheduler_snapshot", concurrent_publish)
    asyncio.run(scheduler._tick(None, factory, 0))
    assert post_mock.await_count == 1


def test_disabled_chat_during_scan_is_not_sent(setup, monkeypatch):
    factory, _, _ = setup
    seed(factory, q1=False)
    original = scheduler.load_scheduler_snapshot

    def disable(db):
        snapshot = original(db)
        db.get(Chat, -100).is_enabled = False
        return snapshot

    monkeypatch.setattr(scheduler, "load_scheduler_snapshot", disable)
    post = AsyncMock()
    monkeypatch.setattr(scheduler, "_post_q1", post)
    asyncio.run(scheduler._tick(None, factory, 0))
    post.assert_not_awaited()


@pytest.mark.parametrize("local,notifications", [
    (datetime(2026, 10, 20, 0, 2), False),
    (datetime(2026, 10, 20, 0, 6), False),
    (datetime(2026, 10, 19, 23, 30), True),
    (datetime(2026, 10, 19, 23, 55), False),
    (datetime(2026, 11, 19, 12), True),
    (datetime(2027, 2, 9, 12), True),
])
def test_special_windows_keep_existing_worker(setup, monkeypatch, local, notifications):
    factory, _, clock = setup
    clock[0] = local.replace(tzinfo=ZoneInfo("Europe/Minsk"))
    seed(factory, day=local.date(), notifications=notifications)
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, 0))
    worker.assert_awaited_once()


def test_missed_close_is_processed_even_without_notifications(setup, monkeypatch):
    factory, _, _ = setup
    seed(factory, notifications=False)
    with db_session(factory) as db:
        db.add(DaySession(chat_id=-100, session_date=date(2026, 10, 18), status="active"))

    async def close(bot, db, chat_id, session_id, tz):
        db.get(DaySession, session_id).status = "closed"

    close_mock = AsyncMock(side_effect=close)
    monkeypatch.setattr(scheduler, "_close_session", close_mock)
    asyncio.run(scheduler._tick(None, factory, 0))
    asyncio.run(scheduler._tick(None, factory, 0))
    close_mock.assert_awaited_once()


@pytest.mark.parametrize("today,kinds", [
    (date(2026, 10, 19), {"weekly_stats"}),
    (date(2026, 11, 1), {"monthly_stats"}),
    (date(2027, 1, 1), {"monthly_stats", "yearly_stats"}),
    (date(2024, 1, 1), {"weekly_stats", "monthly_stats", "yearly_stats"}),
])
def test_all_due_reports_sent_once(setup, monkeypatch, today, kinds):
    factory, _, clock = setup
    clock[0] = datetime.combine(today, time(10), tzinfo=timezone.utc)
    seed(factory, day=today, reports=False)
    from app.services import scheduler_reports
    monkeypatch.setattr(scheduler_reports, "build_periodic_report_text", lambda db, **kw: "report")
    send = AsyncMock(return_value=type("Sent", (), {"message_id": 123})())
    monkeypatch.setattr(scheduler_reports, "safe_send_message", send)
    asyncio.run(scheduler._tick(None, factory, 0))
    asyncio.run(scheduler._tick(None, factory, 0))
    send.assert_not_awaited()
    from app.db.models import WeeklyReport
    with factory() as db:
        assert len(db.scalars(select(WeeklyReport)).all()) == len(kinds)
    with db_session(factory) as db:
        assert set(db.scalars(select(CommandMessage.command))) == kinds


def test_timezones_and_clock_boundary_after_scan(setup, monkeypatch):
    factory, _, clock = setup
    clock[0] = datetime(2026, 10, 19, 21, 29, tzinfo=timezone.utc)
    seed(factory, -100, day=date(2026, 10, 20), tz="Europe/Minsk")
    seed(factory, -200, tz="America/New_York")
    original = scheduler.load_scheduler_snapshot

    def advance(db):
        snapshot = original(db)
        clock[0] += timedelta(minutes=1)
        return snapshot

    monkeypatch.setattr(scheduler, "load_scheduler_snapshot", advance)
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, 0))
    worker.assert_not_awaited()
    # The next local day must be handled even if it started during the scan.
    clock[0] = datetime(2026, 10, 19, 20, 59, tzinfo=timezone.utc)
    asyncio.run(scheduler._tick(None, factory, 0))
    assert [c.args[2] for c in worker.await_args_list] == [-100]


def test_invalid_timezone_does_not_block_other_chats(setup, monkeypatch):
    factory, _, _ = setup
    seed(factory, -100, tz="invalid/timezone")
    seed(factory, -200, q1=False)
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, 0))
    assert [c.args[2] for c in worker.await_args_list] == [-200]


def test_non_report_day_needs_only_two_reads(setup, monkeypatch):
    factory, engine, clock = setup
    clock[0] += timedelta(days=1)
    seed(factory, day=clock[0].date())
    sql = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, s, p, ctx, many: sql.append(s))
    worker = AsyncMock()
    monkeypatch.setattr(scheduler, "_process_chat", worker)
    asyncio.run(scheduler._tick(None, factory, 0))
    worker.assert_not_awaited()
    assert len(sql) == 2


def test_failed_send_is_retried_without_blocking_other_chats(setup, monkeypatch):
    factory, _, _ = setup
    seed(factory, -100, q1=False)
    seed(factory, -200, q1=False)
    failed_once = False

    async def post(bot, db, chat_id, session_id, *args, **kwargs):
        nonlocal failed_once
        if chat_id == -100 and not failed_once:
            failed_once = True
            raise TimeoutError("temporary send failure")
        db.add(SessionMessage(session_id=session_id, kind="Q1", message_id=123))

    post_mock = AsyncMock(side_effect=post)
    monkeypatch.setattr(scheduler, "_post_q1", post_mock)
    asyncio.run(scheduler._tick(None, factory, 0))
    asyncio.run(scheduler._tick(None, factory, 0))
    asyncio.run(scheduler._tick(None, factory, 0))
    assert post_mock.await_count == 3
    with db_session(factory) as db:
        assert len(list(db.scalars(select(SessionMessage)))) == 2


def test_reminder_boundary_rechecked_after_scan(setup, monkeypatch):
    factory, _, clock = setup
    clock[0] = datetime(2026, 10, 19, 20, 29, tzinfo=timezone.utc)
    seed(factory)
    original = scheduler.load_scheduler_snapshot

    def advance(db):
        snapshot = original(db)
        clock[0] += timedelta(minutes=1)
        return snapshot

    monkeypatch.setattr(scheduler, "load_scheduler_snapshot", advance)
    reminder = AsyncMock()
    monkeypatch.setattr(scheduler, "_send_late_reminder", reminder)
    asyncio.run(scheduler._tick(None, factory, 0))
    reminder.assert_awaited_once()
