from datetime import date
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
    assert press(42)["s"]["d"][0] == 1
    assert press(42)["s"]["d"][0] == 1


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
