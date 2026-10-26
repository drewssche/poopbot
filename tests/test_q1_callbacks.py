import asyncio
import logging
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.bot.handlers import callbacks_q1 as handler
from app.db.base import Base
from app.db.models import (
    Chat, ChatMember, CommandMessage, PoopEvent, Session as DaySession,
    SessionMessage, SessionUserState, User, UserStreak,
)
from app.services.reminder_service import LATE_REMINDER_COMMAND
from app.services.time_service import SessionWindow


@pytest.fixture
def scenario(tmp_path, monkeypatch, caplog):
    # Separate connections let the Telegram stub verify actual committed data.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'q1.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    today = date(2026, 9, 29)
    with factory.begin() as db:
        db.add_all([
            Chat(chat_id=-100), User(user_id=1, first_name="Test"),
            DaySession(session_id=1, chat_id=-100, session_date=today, status="active"),
            SessionMessage(session_id=1, kind="Q1", message_id=10),
        ])
    monkeypatch.setattr(handler, "init_db", lambda _: None)
    monkeypatch.setattr(handler, "_session_factory", factory)
    monkeypatch.setattr("app.core.config.load_settings", lambda: SimpleNamespace(database_url="unused"))
    monkeypatch.setattr(handler, "get_session_window", lambda _: SessionWindow(today, False, True))
    caplog.set_level(logging.INFO, logger=handler.__name__)
    cb = SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"), message_id=10, reply_to_message=None,
        ),
        from_user=SimpleNamespace(id=1, username="test", first_name="Test", last_name=None),
        data="q1:plus", answer=AsyncMock(),
        bot=SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock()),
    )
    yield SimpleNamespace(factory=factory, today=today, cb=cb)
    engine.dispose()


def assert_mark(factory, session_id, count):
    with factory() as db:
        state = db.get(SessionUserState, (session_id, 1))
        assert state is not None
        assert state.poops_n == count
        events = db.scalars(select(PoopEvent).where(
            PoopEvent.session_id == session_id, PoopEvent.user_id == 1,
        )).all()
        assert len(events) == count
        assert all(ev.origin_chat_id == -100 for ev in events)


@pytest.mark.parametrize("action", ["q1:plus", "q1:minus", "q1:plus_late", "q1:restore_streak"])
@pytest.mark.parametrize("answer_error", [None, "expired", "invalid", "network"])
def test_action_commits_before_answer_and_refreshes_despite_answer_failure(scenario, caplog, action, answer_error):
    s = scenario
    s.cb.data = action
    target_session, count = 1, 1
    with s.factory.begin() as db:
        if action == "q1:minus":
            db.add(SessionUserState(session_id=1, user_id=1, poops_n=1))
            db.add(PoopEvent(session_id=1, user_id=1, event_n=1, origin_chat_id=-100))
            count = 0
        elif action == "q1:plus_late":
            s.cb.message.message_id = 11
            db.add(CommandMessage(chat_id=-100, user_id=0, command=LATE_REMINDER_COMMAND,
                                  session_date=s.today, message_id=11))
        elif action == "q1:restore_streak":
            db.add_all([
                ChatMember(chat_id=-100, user_id=1),
                UserStreak(chat_id=-100, user_id=1),
                DaySession(session_id=2, chat_id=-100, session_date=s.today - timedelta(days=1), status="closed"),
                DaySession(session_id=3, chat_id=-100, session_date=s.today - timedelta(days=2), status="closed"),
                SessionUserState(session_id=3, user_id=1, poops_n=1),
                PoopEvent(session_id=3, user_id=1, event_n=1, origin_chat_id=-100),
            ])
            target_session = 2

    async def answer(*args, **kwargs):
        assert_mark(s.factory, target_session, count)
        method = AnswerCallbackQuery(callback_query_id="test")
        if answer_error == "expired":
            raise TelegramBadRequest(method=method, message="query is too old and response timeout expired")
        if answer_error == "invalid":
            raise TelegramBadRequest(method=method, message="query ID is invalid")
        if answer_error == "network":
            raise TelegramNetworkError(method=method, message="Request timeout")

    s.cb.answer.side_effect = answer
    asyncio.run(handler.q1_callbacks(s.cb))

    assert_mark(s.factory, target_session, count)
    s.cb.answer.assert_awaited_once()
    s.cb.bot.edit_message_text.assert_awaited_once()
    assert s.cb.bot.edit_message_text.await_args.kwargs["message_id"] == 10
    assert "outcome=saved" in caplog.text
    assert "chat_id=-100 user_id=1" in caplog.text
    assert f"action={action}" in caplog.text
    if answer_error:
        assert "Q1 acknowledgement failed" in caplog.text
        assert "committed=true" in caplog.text


def test_private_flow_continues_after_expired_answer(scenario):
    s = scenario
    s.cb.message.chat.type = "private"
    s.cb.answer.side_effect = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="test"), message="query is too old",
    )
    asyncio.run(handler.q1_callbacks(s.cb))
    assert_mark(s.factory, 1, 1)
    assert s.cb.bot.edit_message_text.await_count == 2


def test_commit_failure_does_not_acknowledge_success(scenario, caplog):
    s = scenario

    def fail_commit(session):
        raise SQLAlchemyError("simulated commit failure")

    event.listen(s.factory, "before_commit", fail_commit)
    try:
        asyncio.run(handler.q1_callbacks(s.cb))
    finally:
        event.remove(s.factory, "before_commit", fail_commit)

    with s.factory() as db:
        assert db.get(SessionUserState, (1, 1)) is None
        assert db.scalar(select(PoopEvent.id)) is None
    s.cb.answer.assert_awaited_once_with("Ошибка, попробуй ещё раз", show_alert=False)
    s.cb.bot.edit_message_text.assert_not_awaited()
    assert "outcome=rolled_back" in caplog.text
    assert "outcome=saved" not in caplog.text


@pytest.mark.parametrize("rejection", ["closed_session", "unknown_message", "blocked_window"])
def test_rejected_action_logs_reason_and_creates_no_mark(scenario, monkeypatch, caplog, rejection):
    s = scenario
    if rejection == "closed_session":
        with s.factory.begin() as db:
            db.get(DaySession, 1).status = "closed"
    elif rejection == "unknown_message":
        s.cb.message.message_id = 999
    else:
        monkeypatch.setattr(handler, "get_session_window", lambda _: SessionWindow(s.today, True, False))
    asyncio.run(handler.q1_callbacks(s.cb))
    with s.factory() as db:
        assert db.get(SessionUserState, (1, 1)) is None
        assert db.scalar(select(PoopEvent.id)) is None
    assert f"outcome={rejection}" in caplog.text
    assert "outcome=saved" not in caplog.text


def test_refresh_failure_preserves_mark_without_prompting_duplicate(scenario, caplog):
    s = scenario
    s.cb.bot.edit_message_text.side_effect = TelegramNetworkError(
        method=AnswerCallbackQuery(callback_query_id="test"), message="Request timeout",
    )
    asyncio.run(handler.q1_callbacks(s.cb))
    assert_mark(s.factory, 1, 1)
    assert s.cb.answer.await_args.args[0] == "Изменения сохранены, но сообщение не обновилось"
    assert "outcome=post_commit_error" in caplog.text


def test_photo_question_saves_mark_and_queues_its_existing_message(scenario):
    from app.services.weekly_reports import enqueue_report, ReportWorker
    s = scenario
    s.cb.bot.edit_message_media = AsyncMock()
    s.cb.bot.edit_message_caption = AsyncMock()
    with s.factory.begin() as db:
        row = enqueue_report(db, -100, s.today, chat_title='Тест')
        row.q1_session_id = 1
        row.photo_message_id = 10
        row.card_hash = 'previous-image'
        row.delivered_revision = 1
        rid = row.id
    asyncio.run(handler.q1_callbacks(s.cb))
    assert_mark(s.factory, 1, 1)
    s.cb.bot.edit_message_text.assert_not_awaited()
    s.cb.bot.send_message.assert_not_awaited()
    worker = ReportWorker(s.cb.bot, s.factory, 'unused')
    asyncio.run(worker.deliver(rid))
    assert s.cb.bot.edit_message_media.await_args.kwargs['message_id'] == 10
    assert worker.get(rid).revision == worker.get(rid).delivered_revision


def test_group_plus_plus_minus_keeps_saved_counts_after_photo_refresh(scenario, monkeypatch):
    from app.services.weekly_reports import enqueue_report, ReportWorker
    from app.services.q1_card_state import plain_text
    s = scenario
    monkeypatch.setattr(handler, 'check_rate_limit', lambda *a, **kw: True)
    s.cb.bot.edit_message_media = AsyncMock()
    s.cb.bot.edit_message_caption = AsyncMock()
    with s.factory.begin() as db:
        row = enqueue_report(db, -100, s.today, chat_title='Тест')
        row.q1_session_id = 1
        row.photo_message_id = 10
        row.card_hash = 'previous-image'
        row.delivered_revision = 1
        rid = row.id
    worker = ReportWorker(s.cb.bot, s.factory, 'unused')
    for action, expected in [('q1:plus', 1), ('q1:plus', 2), ('q1:minus', 1), ('q1:minus', 0)]:
        s.cb.data = action
        asyncio.run(handler.q1_callbacks(s.cb))
        assert_mark(s.factory, 1, expected)
        assert f'Твоих отметок за день: {expected}.' in s.cb.answer.await_args.args[0]
        asyncio.run(worker.deliver(rid))
        caption = plain_text(s.cb.bot.edit_message_media.await_args.kwargs['media'].caption)
        participant = next(line for line in caption.splitlines() if line.startswith('@test —'))
        if expected:
            assert f' {expected} |' in participant or f'💩({expected})' in participant
            assert '— —' not in participant
        else:
            assert '— —' in participant
        assert worker.pending()[0] == []
        assert_mark(s.factory, 1, expected)
    s.cb.bot.edit_message_text.assert_not_awaited()
