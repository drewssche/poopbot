from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.bot.handlers import mini_app
from app.services.mini_app_links import scope_chat


@pytest.mark.asyncio
@pytest.mark.parametrize('private',[True,False])
async def test_app_command_uses_private_webapp_or_signed_group_link(monkeypatch,private):
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine)
    monkeypatch.setattr(mini_app,'load_settings',lambda:SimpleNamespace(bot_token='test',database_url='unused'))
    monkeypatch.setattr(mini_app,'get_session_factory',lambda _:factory)
    monkeypatch.setenv('MINI_APP_ORIGIN','https://example.test')
    message=SimpleNamespace(from_user=SimpleNamespace(id=10),
        chat=SimpleNamespace(id=10 if private else -100,type='private' if private else 'supergroup',title='<Chat>'),
        bot=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='testbot'))),answer=AsyncMock())
    await mini_app.app_cmd(message)
    call=message.answer.call_args
    button=call.kwargs['reply_markup'].inline_keyboard[0][0]
    if private:
        assert button.web_app.url == 'https://example.test/poopcheck/' and button.url is None
    else:
        assert button.web_app is None and button.url.startswith('https://t.me/testbot?startapp=')
        assert scope_chat('test',button.url.split('startapp=')[1]) == -100
        assert '&lt;Chat&gt;' in call.args[0]
    engine.dispose()
