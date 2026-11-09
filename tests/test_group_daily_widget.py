import json
from datetime import date, datetime, UTC, timedelta
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, delete
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    AppSetting,
    Chat,
    PoopEvent,
    RateLimit,
    Session as DaySession,
    SessionMessage,
    WeeklyReport,
)
from app.services.group_daily_widget import FLAG, PILOT, mutate, enabled
from app.services.q1_card_service import send_question
from app.services.weekly_reports import ReportWorker
from app.services.mini_app_service import change_mark, event_version
from app.services.time_service import SessionWindow


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    factory = sessionmaker(
        create_engine(f"sqlite:///{tmp_path}/pilot.db"),
        expire_on_commit=False,
        autoflush=False,
    )
    Base.metadata.create_all(factory.kw["bind"])
    day = date.today()
    monkeypatch.setattr(
        "app.services.group_daily_widget.get_session_window",
        lambda _: SessionWindow(day, False, True),
    )
    with factory.begin() as db:
        db.add_all(
            [
                Chat(chat_id=PILOT),
                AppSetting(key=FLAG, value=str(PILOT)),
                DaySession(chat_id=PILOT, session_date=day, status="active"),
            ]
        )
    return factory, day


@pytest.mark.asyncio
async def test_widget_routes_updates_replays_and_private_details(fixture):
    factory, day = fixture
    bot = AsyncMock(return_value=SimpleNamespace(message_id=123))
    with factory.begin() as db:
        sess = db.scalar(select(DaySession))
        assert enabled(db, PILOT) and not enabled(db, -77)
        await send_question(bot, db, chat_id=PILOT, session_id=sess.session_id, text="")
        db.add(SessionMessage(session_id=sess.session_id, kind="Q1", message_id=123))

    def tap(uid, op="+", nonce=1, **overrides):
        q = {
            "chat": {"id": PILOT, "type": "supergroup"},
            "from": {"id": uid},
            "message_id": 123,
            "data": f"q:{op}:{nonce:016x}",
        }
        with factory.begin() as db:
            return mutate(db, dict(q, **overrides))

    a = tap(1)
    b = tap(2)
    assert b["s"]["d"][day.weekday()] == 2
    assert tap(1)["e"] == a["e"]  # lost answer, retry with a new Telegram query
    op = f"e{a['e']}.{a['v']}.21"
    assert "изменилась" in tap(2, op, 2)["t"]
    assert tap(1, op, 2)["t"] == "Уточнения сохранены"
    assert "s" not in tap(3, chat={"type": "group", "id": -99})
    assert "s" not in tap(3, message_id=99)
    assert "s" not in tap(3, chat={})
    with factory.begin() as db:
        ev = db.get(PoopEvent, a["e"])
        assert (ev.bristol, ev.feeling) == (6, "ok")
        # A real Mini App deletion is projected into the same widget.
        change_mark(
            db,
            PILOT,
            1,
            {
                "action": "delete",
                "eventId": str(ev.id),
                "requestId": "12345678-0000-4000-8000-000000000001",
                "version": event_version(ev),
                "date": day.isoformat(),
            },
            now=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=10),
        )
        db.execute(delete(RateLimit))
        row = db.scalar(select(WeeklyReport))
        row.revision += 1
        rid = row.id
    worker = ReportWorker(bot, factory, "", None)
    await worker.deliver(rid)
    assert json.loads(bot.await_args.args[0].chat_data)["d"][day.weekday()] == 1
    assert tap(2, "-", 3)["s"]["d"][day.weekday()] == 0
    with factory.begin() as db:
        db.scalar(select(DaySession)).status = "closed"
    assert tap(1, "+", 4)["t"] == "Сессия закрыта"
    assert tap(1, "f", 5)["s"]["d"][day.weekday()] == 1


def test_simultaneous_first_marks_and_retries_postgres(monkeypatch):
    """Exercise the real event triggers and locks in an isolated migrated database."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    url = os.environ.get("WIDGET_TEST_DATABASE_URL")
    if not url:
        pytest.skip("disposable migrated PostgreSQL required")
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC"})
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    day = date.today()
    monkeypatch.setattr(
        "app.services.group_daily_widget.get_session_window",
        lambda _: SessionWindow(day, False, True),
    )
    with factory.begin() as db:
        assert not db.scalar(select(Chat.chat_id).limit(1))
        db.add_all([Chat(chat_id=PILOT), AppSetting(key=FLAG, value=str(PILOT))])
        db.flush()
        s = DaySession(chat_id=PILOT, session_date=day, status="active")
        db.add(s)
        db.flush()
        db.add(SessionMessage(session_id=s.session_id, kind="Q1", message_id=123))
        from app.services.group_daily_widget import send
        import asyncio

        asyncio.run(
            send(AsyncMock(return_value=SimpleNamespace(message_id=123)), db, s)
        )

    def tap(uid):
        with factory.begin() as db:
            return mutate(
                db,
                {
                    "from": {"id": uid},
                    "chat": {"id": PILOT, "type": "supergroup"},
                    "message_id": 123,
                    "data": "q:+:0000000000000001",
                },
            )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            res = list(pool.map(tap, [11, 12, 11, 12]))
        assert len({a["e"] for a in res}) == 2
        with factory() as db:
            assert len(db.scalars(select(PoopEvent)).all()) == 2
            r = db.scalar(select(WeeklyReport))
            assert r.revision > 1 and r.delivered_revision < r.revision
    finally:
        from sqlalchemy import text

        with engine.begin() as db:
            db.execute(text("TRUNCATE chats, users, rate_limits CASCADE"))
            db.execute(text("DELETE FROM app_settings WHERE key=:key"), {"key": FLAG})
        engine.dispose()
