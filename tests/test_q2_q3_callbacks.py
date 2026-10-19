import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.bot.handlers import callbacks_q2, callbacks_q3
from app.db.base import Base
from app.db.models import Chat, User, Session as DaySession, SessionMessage, SessionUserState, PoopEvent
from app.services.time_service import SessionWindow


@pytest.fixture(params=['q2', 'q3'])
def scenario(request, tmp_path, monkeypatch):
    kind = request.param
    handler = callbacks_q2 if kind == 'q2' else callbacks_q3
    field, value, choice = ('bristol', 4, '34') if kind == 'q2' else ('feeling', 'great', 'great')
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'answers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    today = date(2026, 10, 19)
    with factory.begin() as db:
        db.add_all([
            Chat(chat_id=1), User(user_id=1, first_name='Test'),
            DaySession(session_id=1, chat_id=1, session_date=today, status='active'),
            SessionMessage(session_id=1, kind='Q1', message_id=10),
            SessionMessage(session_id=1, kind='Q2', message_id=20),
            SessionMessage(session_id=1, kind='Q3', message_id=30),
            SessionUserState(session_id=1, user_id=1, poops_n=1),
            PoopEvent(session_id=1, user_id=1, event_n=1, origin_chat_id=1),
        ])
    monkeypatch.setattr(handler, 'init_db', lambda _: None)
    monkeypatch.setattr(handler, '_session_factory', factory)
    monkeypatch.setattr('app.core.config.load_settings', lambda: SimpleNamespace(database_url='unused'))
    monkeypatch.setattr(handler, 'get_session_window', lambda _: SessionWindow(today, False, True))
    cb = SimpleNamespace(
        data=f'{kind}:set:1:{choice}',
        from_user=SimpleNamespace(id=1, username=None, first_name='Test', last_name=None),
        message=SimpleNamespace(chat=SimpleNamespace(id=1, type='private'), message_id=10, edit_text=AsyncMock()),
        answer=AsyncMock(), bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )
    yield SimpleNamespace(kind=kind, run=getattr(handler, f'{kind}_callbacks'), cb=cb, factory=factory, field=field, value=value)
    engine.dispose()


def assert_value(s, expected):
    with s.factory() as db:
        assert getattr(db.scalar(select(PoopEvent)), s.field) == expected
        assert getattr(db.get(SessionUserState, (1, 1)), s.field) == expected
        assert db.get(SessionUserState, (1, 1)).poops_n == 1


@pytest.mark.parametrize('chat_type', ['private', 'supergroup'])
@pytest.mark.parametrize('answer_error', [None, 'expired', 'invalid', 'network'])
def test_save_before_answer_and_continue_refresh(scenario, chat_type, answer_error):
    s = scenario
    s.cb.message.chat.type = chat_type
    if chat_type != 'private':
        s.cb.message.message_id = 20 if s.kind == 'q2' else 30

    async def answer(*args, **kwargs):
        assert_value(s, s.value)  # Read through a separate DB connection.
        method = AnswerCallbackQuery(callback_query_id='test')
        if answer_error == 'expired':
            raise TelegramBadRequest(method=method, message='query is too old')
        if answer_error == 'invalid':
            raise TelegramBadRequest(method=method, message='query ID is invalid')
        if answer_error == 'network':
            raise TelegramNetworkError(method=method, message='Request timeout')

    s.cb.answer.side_effect = answer
    asyncio.run(s.run(s.cb))
    assert_value(s, s.value)
    s.cb.answer.assert_awaited_once()
    s.cb.message.edit_text.assert_awaited_once()
    if chat_type != 'private':
        s.cb.bot.edit_message_text.assert_awaited_once()


def test_commit_failure_does_not_confirm_success(scenario):
    s = scenario

    def fail_commit(db):
        # Only the transaction that is trying to save the choice fails.
        if any(isinstance(row, PoopEvent) and getattr(row, s.field) == s.value for row in db.dirty):
            raise SQLAlchemyError('simulated write failure')

    event.listen(s.factory, 'before_commit', fail_commit)
    try:
        asyncio.run(s.run(s.cb))
    finally:
        event.remove(s.factory, 'before_commit', fail_commit)
    assert_value(s, None)
    s.cb.answer.assert_awaited_once_with('Не удалось сохранить ответ. Попробуй ещё раз.', show_alert=False)
    s.cb.message.edit_text.assert_not_awaited()
    s.cb.bot.edit_message_text.assert_not_awaited()


def test_failed_group_question_edit_does_not_lose_choice_or_block_q1_refresh(scenario):
    s = scenario
    s.cb.message.chat.type = 'supergroup'
    s.cb.message.message_id = 20 if s.kind == 'q2' else 30
    s.cb.message.edit_text.side_effect = TelegramNetworkError(method=EditMessageText(text='test'), message='Request timeout')
    asyncio.run(s.run(s.cb))
    assert_value(s, s.value)
    s.cb.bot.edit_message_text.assert_awaited_once()


@pytest.mark.parametrize('navigation', ['skip', 'back'])
def test_expired_navigation_answer_still_opens_next_screen(scenario, navigation):
    s = scenario
    if navigation == 'skip':
        s.cb.data = f'{s.kind}:skip:1'
    else:
        s.cb.data = 'q2:back_q1' if s.kind == 'q2' else 'q3:back_q2:1'
    s.cb.answer.side_effect = TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id='test'), message='query is too old')
    asyncio.run(s.run(s.cb))
    assert_value(s, None)
    s.cb.message.edit_text.assert_awaited_once()
