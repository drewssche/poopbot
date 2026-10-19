import asyncio
from datetime import date
from html import unescape
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetUpdates
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import runtime
from app.db.base import Base
from app.db.models import Chat, ChatMember, User
from app.bot import dispatcher, monitoring
from app.services.q1_service import mention, render_q1, render_q1_private
from app.services.recap_cards import _user_label
from app.services.reminder_service import _user_mention_html


def test_all_handlers_share_configured_pool(monkeypatch):
    engine = Mock()
    factory = Mock()
    make = Mock(return_value=engine)
    monkeypatch.setattr(runtime, 'make_engine', make)
    monkeypatch.setattr(runtime, 'make_session_factory', Mock(return_value=factory))
    settings = Settings(bot_token='test', database_url='postgresql://test', db_pool_size=3, db_max_overflow=1)
    try:
        assert runtime.configure_database(settings) is factory
        for name in ['commands','callbacks_q1','callbacks_q2','callbacks_q3','callbacks_restore',
                     'callbacks_streak_admin','callbacks_help','callbacks_stats','callbacks_recap','callbacks_debug']:
            module = importlib.import_module('app.bot.handlers.'+name)
            monkeypatch.setattr(module,'_session_factory',None)
            module.init_db(settings.database_url)
            assert module._session_factory is factory
        make.assert_called_once_with(settings.database_url,pool_size=3,max_overflow=1,pool_timeout_sec=10,pool_recycle_sec=1800)
        with pytest.raises(RuntimeError):
            runtime.get_session_factory('postgresql://different')
    finally:
        runtime.close_database()
    engine.dispose.assert_called_once()
    with pytest.raises(RuntimeError):
        runtime.get_session_factory(settings.database_url)


def test_runtime_resources_close_even_when_startup_fails(monkeypatch):
    bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(dispatcher,'Bot',Mock(return_value=bot))
    monkeypatch.setattr(dispatcher,'configure_database',Mock(return_value=object()))
    close = Mock()
    monkeypatch.setattr(dispatcher,'close_database',close)
    monkeypatch.setattr(dispatcher,'_run_bot',AsyncMock(side_effect=RuntimeError('startup failed')))
    with pytest.raises(RuntimeError,match='startup failed'):
        asyncio.run(dispatcher.run_bot(Settings(bot_token='test',database_url='test')))
    bot.session.close.assert_awaited_once()
    close.assert_called_once()


def test_actual_aiogram_session_tracks_empty_successful_polls_only():
    class SessionStub(BaseSession):
        fail = False
        async def close(self): pass
        async def make_request(self, bot, method, timeout=None):
            if self.fail:
                raise TimeoutError('network unavailable')
            return []
        async def stream_content(self, *args, **kwargs):
            yield b''
    async def run():
        session = SessionStub()
        success = Mock()
        session.middleware(monitoring.PollingActivityMiddleware(success))
        bot = Bot('123456:FAKE_TOKEN_FOR_TEST_ONLY',session=session)
        assert await bot.get_updates() == []
        success.assert_called_once()
        session.fail = True
        with pytest.raises(TimeoutError):
            await bot.get_updates()
        success.assert_called_once()
        await bot.session.close()
    asyncio.run(run())


def test_pending_guard_raises_for_stalled_polling(monkeypatch):
    monkeypatch.setattr(monitoring.asyncio,'sleep',AsyncMock())
    monkeypatch.setattr(monitoring.time,'monotonic',lambda:1000)
    bot = SimpleNamespace(get_webhook_info=AsyncMock(return_value=SimpleNamespace(url='',pending_update_count=6)))
    settings = Settings(bot_token='test',database_url='test')
    with pytest.raises(RuntimeError,match='stalled'):
        asyncio.run(monitoring.telegram_guard_loop(bot,settings,lambda:0))
    bot.get_webhook_info.assert_awaited_once()


def test_webhook_recovery_preserves_updates(monkeypatch):
    monkeypatch.setattr(monitoring.asyncio,'sleep',AsyncMock(side_effect=[None,asyncio.CancelledError()]))
    monkeypatch.setattr(monitoring.time,'monotonic',lambda:1000)
    bot = SimpleNamespace(get_webhook_info=AsyncMock(return_value=SimpleNamespace(url='https://example.test',pending_update_count=6)),
                          delete_webhook=AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitoring.telegram_guard_loop(bot,Settings(bot_token='test',database_url='test'),lambda:0))
    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)


@pytest.mark.parametrize('fail_delete',[False,True])
def test_repeated_network_failure_requests_restart(monkeypatch,fail_delete):
    monkeypatch.setattr(monitoring.asyncio,'sleep',AsyncMock())
    monkeypatch.setattr(monitoring.time,'monotonic',lambda:1000)
    bot = SimpleNamespace(get_webhook_info=AsyncMock(return_value=SimpleNamespace(url='https://example.test',pending_update_count=0)),
                          delete_webhook=AsyncMock(side_effect=TimeoutError()))
    if not fail_delete:
        bot.get_webhook_info.side_effect=TimeoutError()
    settings=Settings(bot_token='test',database_url='test',polling_guard_network_failures_to_restart=2)
    with pytest.raises(RuntimeError,match='Repeated Telegram'):
        asyncio.run(monitoring.telegram_guard_loop(bot,settings,lambda:0))
    assert bot.get_webhook_info.await_count == 2


@pytest.mark.parametrize('disabled',[True,False])
def test_user_names_cannot_inject_html(disabled):
    user = User(user_id=1,first_name='<b>A&B</b>',last_name='"Example"',disable_mentions=disabled)
    for rendered in [mention(user),_user_label(user,1),_user_mention_html(user,1)]:
        assert '<b>' not in rendered and '&lt;b&gt;A&amp;B&lt;/b&gt;' in rendered
        assert '<b>A&B</b>' in unescape(rendered)
    assert ('tg://user?id=1' in _user_mention_html(user,1)) is (not disabled)


def test_closed_sessions_do_not_offer_removed_buttons():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Chat(chat_id=-1),Chat(chat_id=1),User(user_id=1,first_name='<Sam>')])
        db.flush()
        db.add(ChatMember(chat_id=-1,user_id=1))
        db.flush()
        for render, args in [(render_q1,dict(chat_id=-1)),(render_q1_private,dict(chat_id=1,user_id=1))]:
            kwargs=dict(db=db,session_id=123,session_date=date(2026,10,19),**args)
            assert 'нажми' in render(**kwargs).lower()
            closed=render(**kwargs,closed=True)
            assert 'нажми' not in closed.lower() and '+💩' not in closed
            assert '19.10.26' in closed
    engine.dispose()
