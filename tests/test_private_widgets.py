from datetime import UTC, date, datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    AppSetting,
    Chat,
    PoopEvent,
    Session as DaySession,
    User,
    WeeklyReport,
)
from app.services.group_daily_widget import PRIVATE_FLAG, mutate
from app.services.scheduler_service import _post_q1
from app.services.time_service import SessionWindow
from app.services.weekly_reports import enqueue_report, ReportWorker

DAY = date(2026, 11, 9)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/personal.db")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with sessions.begin() as db:
        db.add_all(
            [
                AppSetting(key=PRIVATE_FLAG, value="true"),
                Chat(chat_id=42),
                User(user_id=42),
                User(user_id=43),
            ]
        )
        db.flush()
        sess = DaySession(chat_id=42, session_date=DAY, status="active")
        db.add(sess)
        db.flush()
        db.add(
            PoopEvent(
                session_id=sess.session_id, user_id=43, origin_chat_id=42, event_n=1
            )
        )
    monkeypatch.setattr(
        "app.services.group_daily_widget.get_session_window",
        lambda _: SessionWindow(DAY, False, True),
    )
    return sessions


@pytest.mark.asyncio
async def test_scheduled_private_question_is_scoped_to_owner(factory):
    bot = AsyncMock(return_value=SimpleNamespace(message_id=13))
    with factory.begin() as db:
        sess = db.scalar(select(DaySession))
        await _post_q1(bot, db, 42, sess.session_id, DAY, False)
    sent = bot.await_args.args[0]
    assert sent.__api_method__ == "sendWidget" and "Личная сессия" in sent.html
    assert json.loads(sent.chat_data)["d"] == [0] * 7

    def press(user, chat=42, msg=13):
        with factory.begin() as db:
            return mutate(
                db,
                {
                    "from": {"id": user},
                    "chat": {"type": "private", "id": chat},
                    "message_id": msg,
                    "data": "q:+:0123456789abcdef",
                },
            )

    assert "s" not in press(43)
    assert "s" not in press(43, 43)
    assert "s" not in press(42, msg=14)
    reply = press(42)
    assert reply["s"]["d"][0] == 1
    assert press(42)["e"] == reply["e"]
    worker = ReportWorker(bot, factory, "", None)
    with factory() as db:
        report_id = db.scalar(select(WeeklyReport.id))
    await worker.deliver(report_id)
    state = json.loads(bot.await_args.args[0].chat_data)
    assert state["d"][0] == 1
    assert state["ack"] == {"n": "0123456789abcdef", "t": reply["t"], "e": reply["e"], "v": reply["v"]}


@pytest.mark.asyncio
async def test_widget_choices_record_one_complete_personal_mark(factory, monkeypatch):
    from app.services import mini_app_service

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 12, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(mini_app_service, "datetime", Clock)
    bot = AsyncMock(return_value=SimpleNamespace(message_id=13))
    with factory.begin() as db:
        sess = db.scalar(select(DaySession))
        await _post_q1(bot, db, 42, sess.session_id, DAY, False)

    query = {"from": {"id": 42}, "chat": {"type": "private", "id": 42},
             "message_id": 13, "data": "q:a12:0123456789abcdef"}
    for _ in range(2):
        with factory.begin() as db:
            assert mutate(db, query)["s"]["d"][0] == 1
    with factory.begin() as db:
        event, = db.scalars(select(PoopEvent).where(PoopEvent.user_id == 42)).all()
        assert (event.bristol, event.feeling) == (4, "bad")


@pytest.mark.parametrize("period", ["week", "month", "year"])
@pytest.mark.asyncio
async def test_private_report_sends_then_updates_same_widget(factory, period):
    bot = AsyncMock(return_value=SimpleNamespace(message_id=27))
    worker = ReportWorker(bot, factory, "", None)
    with factory.begin() as db:
        row = enqueue_report(db, 42, DAY, period=period, scheduled=True)
        rid = row.id
        assert row.widget_report
        assert not enqueue_report(
            db, 42, DAY, period=period, destination=-11
        ).widget_report
        assert not enqueue_report(
            db, -11, DAY, period=period, destination=42
        ).widget_report
    await worker.deliver(rid)
    send = bot.await_args.args[0]
    assert send.__api_method__ == "sendWidget" and not send.allow_queries
    assert json.loads(send.chat_data)["total"] == 0
    with factory.begin() as db:
        row = db.get(WeeklyReport, rid)
        assert row.photo_message_id == 27 and row.delivered_revision == row.revision
        row.revision += 1  # SQLite has no Postgres invalidation trigger
        db.add(
            PoopEvent(
                session_id=db.scalar(select(DaySession.session_id)),
                origin_chat_id=42,
                user_id=42,
                event_n=1,
            )
        )
    await worker.deliver(rid)
    edit = bot.await_args.args[0]
    assert edit.__api_method__ == "editMessageWidgetData" and edit.message_id == 27
    updated = json.loads(edit.chat_data)
    assert updated["total"] == 1 and updated["animal"] and sum(updated["counts"]) == 1
    assert len(edit.chat_data.encode()) <= 1024


@pytest.mark.asyncio
async def test_personal_history_scopes_calendar_edits_and_targeted_deletes(factory, monkeypatch):
    from datetime import timedelta
    from app.services.group_daily_widget import encode
    from app.services.q1_service import apply_plus
    from app.services.mini_app_service import event_version
    from app.services import mini_app_service

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 12, tzinfo=UTC).astimezone(tz)
    monkeypatch.setattr(mini_app_service, "datetime", Clock)
    bot = AsyncMock(return_value=SimpleNamespace(message_id=13))
    with factory.begin() as db:
        current = db.scalar(select(DaySession))
        await _post_q1(bot, db, 42, current.session_id, DAY, False)
        past = DaySession(chat_id=42, session_date=DAY-timedelta(days=1), status="closed")
        db.add(past)
        db.flush()
        for _ in range(5):
            apply_plus(db, past.session_id, 42, 42)
        other = db.scalar(select(PoopEvent).where(PoopEvent.user_id == 43))
        foreign = f"i{other.id}.{event_version(other)[:16]}"
    def press(data):
        with factory.begin() as db:
            result = mutate(db, {"from":{"id":42}, "chat":{"type":"private","id":42},
                                 "message_id":13,"data":"q:"+data})
            assert len(encode(result).encode()) <= 1024
            return result
    assert press("v:m")["marked"] == [8]
    assert press("v:m202610")["days"] == 31
    assert "t" in press("v:d20261110")
    assert "t" in press("v:"+foreign)
    assert not press("v:d20261107")["items"]
    items = press("v:d20261108.1")["items"]
    assert len(items) == 2
    key = items[0][1]
    editor = press("v:"+key)
    kinds = press("v:"+editor["items"][0][1])
    feelings = press("v:"+kinds["items"][1][1])
    edit = feelings["items"][1][1][1:]
    assert press(edit+":0000000000000001")["t"] == "Уточнения сохранены"
    assert "изменилась" in press("D"+key[1:]+":0000000000000002")["t"]
    newer = press("v:d20261108.1")["items"][0]
    assert "Норма" in newer[0] and "Сойдёт" in newer[0]
    detail = press("v:"+newer[1])
    confirm = press("v:"+detail["items"][1][1])
    deletion = confirm["items"][0][1][1:]+":0000000000000003"
    assert press(deletion)["t"] == "Отметка удалена"
    assert press(deletion)["s"]["d"][0] == 0
    with factory() as db:
        remaining = db.scalars(select(PoopEvent).where(PoopEvent.user_id==42).order_by(PoopEvent.event_n)).all()
        assert [e.event_n for e in remaining] == [1,2,3,4]
        assert all(e.id != int(key[1:].split('.')[0]) for e in remaining)
