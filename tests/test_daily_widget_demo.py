import json
from datetime import date
from unittest.mock import AsyncMock

from aiogram import Dispatcher
from aiogram.types import Update
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AppSetting
from app.services.daily_widget_demo import AnswerWidget, DemoQueries, EditWidget, KEY, render_demo


@pytest.mark.asyncio
async def test_owner_pilot_dispatch_replay_and_scope():
    sessions = sessionmaker(create_engine('sqlite://'))
    AppSetting.__table__.create(sessions.kw['bind'])
    with sessions.begin() as db:
        db.add(AppSetting(key=KEY, value='{"message":12,"n":1,"r":0}'))
    dp = Dispatcher()
    dp.update.outer_middleware(DemoQueries(42, sessions))
    bot = AsyncMock()
    bot.id = 456

    async def tap(rev, sign='+', user=42, msg=12, kind='private'):
        bot.reset_mock()
        update = Update.model_validate({'update_id': 1, 'widget_query': {'id':'abc', 'from':{'id':user},
            'chat':{'id':42, 'type':kind}, 'message_id':msg, 'data':f'demo:{rev}:{sign}'}}, context={'bot':bot})
        await dp.feed_update(bot, update)
        return [c.args[0] for c in bot.await_args_list]

    for rev, sign, n, r in [(0,'+',2,1), (0,'+',2,1), (1,'-',1,2), (2,'-',0,3)]:
        edit, answer = await tap(rev, sign)
        assert isinstance(edit, EditWidget) and edit.message_id == 12
        assert isinstance(answer, AnswerWidget) and json.loads(edit.chat_data) == {'n':n, 'r':r}
        assert edit.chat_data == answer.data
    for args in ({'user':9}, {'kind':'group'}, {'msg':99}):
        (denied,) = await tap(3, **args)
        assert isinstance(denied, AnswerWidget) and denied.data is None
    with sessions() as db:
        assert json.loads(db.get(AppSetting, KEY).value) == {'message':12,'n':0,'r':3}


def test_week_crosses_new_year_and_fits_budget():
    html = render_demo(date(2027,1,1))
    assert '1 января' in html and '<b>28</b>' in html and '<b>3</b>' in html
    assert html.count('id="now"') == 1 and html.count('id="badge"') == 1
