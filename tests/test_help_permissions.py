import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetChatAdministrators
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bot.handlers import callbacks_help as handler
from app.db.base import Base
from app.db.models import Chat, User


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'permissions.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([Chat(chat_id=-100), User(user_id=1, first_name='Member'), User(user_id=2, first_name='Admin')])
    monkeypatch.setattr(handler, 'init_db', lambda _: None)
    monkeypatch.setattr(handler, '_session_factory', factory)
    monkeypatch.setattr('app.core.config.load_settings', lambda: SimpleNamespace(database_url='unused'))
    monkeypatch.setattr(handler, 'render_notifications_panel', AsyncMock())
    monkeypatch.setattr(handler, 'render_global_visibility_panel', AsyncMock())
    monkeypatch.setattr(handler, 'refresh_q1_after_settings_change', AsyncMock())
    cb = SimpleNamespace(
        data='help:notifications_toggle:2', from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100, type='supergroup'), message_id=40, edit_text=AsyncMock()),
        answer=AsyncMock(), bot=SimpleNamespace(get_chat_administrators=AsyncMock(return_value=[SimpleNamespace(user=SimpleNamespace(id=2))])),
    )
    yield SimpleNamespace(cb=cb, factory=factory)
    engine.dispose()


SHARED_ACTIONS = [
    ('notifications_toggle', 'notifications_enabled', False),
    ('notifications_on', 'notifications_enabled', False),
    ('notifications_off', 'notifications_enabled', False),
    ('late_reminder_toggle', 'late_reminder_enabled', False),
    ('q2_q3_toggle', 'q2_q3_enabled', True),
    ('global_vis_toggle', 'show_in_global', False),
    ('global_vis_on', 'show_in_global', False),
    ('global_vis_off', 'show_in_global', False),
    ('time:19', 'post_time', time(19, 0)),
]


@pytest.mark.parametrize('action,field,new_value', SHARED_ACTIONS)
def test_member_cannot_change_any_shared_setting_even_from_admin_menu(scenario, action, field, new_value, caplog):
    s = scenario
    s.cb.data = f'help:{action}:2'
    with s.factory() as db:
        previous = getattr(db.get(Chat, -100), field)
    asyncio.run(handler.help_callbacks(s.cb))
    with s.factory() as db:
        assert getattr(db.get(Chat, -100), field) == previous
    s.cb.answer.assert_awaited_once_with('Общие настройки группы может менять только её администратор.', show_alert=True)
    handler.render_notifications_panel.assert_not_awaited()
    handler.render_global_visibility_panel.assert_not_awaited()
    assert 'chat_admin_required' in caplog.text and '"actor_id":1' in caplog.text


@pytest.mark.parametrize('action,field,new_value', SHARED_ACTIONS)
def test_actual_admin_can_change_shared_setting_from_another_users_menu(scenario, action, field, new_value):
    s = scenario
    s.cb.bot.get_chat_administrators.return_value = [SimpleNamespace(user=SimpleNamespace(id=1))]
    s.cb.data = f'help:{action}:2'
    asyncio.run(handler.help_callbacks(s.cb))
    with s.factory() as db:
        assert getattr(db.get(Chat, -100), field) == new_value
    s.cb.bot.get_chat_administrators.assert_awaited_once_with(chat_id=-100)


@pytest.mark.parametrize('error_cls', [TelegramBadRequest, TelegramNetworkError])
def test_failed_permission_check_denies_change(scenario, error_cls):
    s = scenario
    s.cb.bot.get_chat_administrators.side_effect = error_cls(method=GetChatAdministrators(chat_id=-100), message='unavailable')
    asyncio.run(handler.help_callbacks(s.cb))
    with s.factory() as db:
        assert db.get(Chat, -100).notifications_enabled
    s.cb.answer.assert_awaited_once_with('Не удалось проверить права. Попробуй ещё раз.', show_alert=True)


def test_revoked_rights_are_checked_again(scenario):
    s = scenario
    s.cb.bot.get_chat_administrators.side_effect = [[SimpleNamespace(user=SimpleNamespace(id=1))], []]
    asyncio.run(handler.help_callbacks(s.cb))
    asyncio.run(handler.help_callbacks(s.cb))
    with s.factory() as db:
        assert not db.get(Chat, -100).notifications_enabled
    assert s.cb.bot.get_chat_administrators.await_count == 2


@pytest.mark.parametrize('action,field,new_value', [SHARED_ACTIONS[i] for i in [0, 3, 4, 8]])
def test_private_settings_do_not_require_admin_check(scenario, action, field, new_value):
    s = scenario
    with s.factory.begin() as db:
        db.add(Chat(chat_id=1))
    s.cb.message.chat.type = 'private'
    s.cb.message.chat.id = 1
    s.cb.data = f'help:{action}:1'
    asyncio.run(handler.help_callbacks(s.cb))
    s.cb.bot.get_chat_administrators.assert_not_awaited()
    with s.factory() as db:
        assert getattr(db.get(Chat, 1), field) == new_value


def test_personal_mention_preference_changes_only_actor(scenario):
    s = scenario
    s.cb.data = 'help:mentions_toggle:2'
    asyncio.run(handler.help_callbacks(s.cb))
    s.cb.bot.get_chat_administrators.assert_not_awaited()
    with s.factory() as db:
        assert db.get(User, 1).disable_mentions
        assert not db.get(User, 2).disable_mentions


@pytest.mark.parametrize('action', ['settings', 'notifications', 'set_time', 'global_vis', 'about', 'back'])
def test_member_can_read_settings_without_admin_check(scenario, action):
    s = scenario
    s.cb.data = f'help:{action}:2'
    asyncio.run(handler.help_callbacks(s.cb))
    s.cb.bot.get_chat_administrators.assert_not_awaited()


def test_member_cannot_confirm_another_users_deletion(scenario, caplog):
    s = scenario
    s.cb.data = 'help:delete_confirm_db:2'
    asyncio.run(handler.help_callbacks(s.cb))
    with s.factory() as db:
        assert db.get(User, 1) is not None
        assert db.get(User, 2) is not None
    s.cb.answer.assert_awaited_once_with('Это не твое подтверждение', show_alert=False)
    assert 'confirmation_owner_required' in caplog.text
