import json
from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.base import BaseSession
from aiogram.filters import Command
from aiogram.types import Message, Update, User
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bot.handlers import callbacks_help, callbacks_stats, commands
from app.bot.menu_widgets import MenuWidgets
from app.db.base import Base
from app.db.models import AppSetting, Chat, PrivateMenu
from app.services.group_daily_widget import PRIVATE_FLAG
from app.services.menu_widget import encode, make_pages, page_state


class Telegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__api_method__ == "getMe":
            return User(id=123456, is_bot=True, first_name="Bot", username="demo_bot")
        if method.__api_method__ in {"sendWidget", "sendMessage"}:
            return Message(
                message_id=len(self.calls),
                date=datetime.now(UTC),
                chat={"id": method.chat_id, "type": "private"},
            )
        return True


@pytest.mark.asyncio
async def test_private_command_navigation_and_replayed_setting(tmp_path, monkeypatch):
    sessions = sessionmaker(create_engine(f"sqlite:///{tmp_path}/menus.db"))
    Base.metadata.create_all(sessions.kw["bind"])
    with sessions.begin() as db:
        db.add(AppSetting(key=PRIVATE_FLAG, value="true"))
    for module in (commands, callbacks_help, callbacks_stats):
        monkeypatch.setattr(module, "get_session_factory", lambda _: sessions)
    monkeypatch.setattr(
        "app.core.config.load_settings",
        lambda: SimpleNamespace(database_url="test", bot_owner_id=None),
    )

    transport = Telegram()
    bot = Bot("123456:TEST", session=transport)
    dp = Dispatcher()
    dp.message.register(commands.help_cmd, Command("help"))
    dp.callback_query.register(
        callbacks_help.help_callbacks, F.data.startswith("help:")
    )
    dp.callback_query.register(
        callbacks_stats.stats_callbacks, F.data.startswith("stats:")
    )
    menus = MenuWidgets(sessions, dp)
    transport.middleware(menus.outgoing)
    dp.update.outer_middleware(menus.incoming)
    actor = {"id": 42, "is_bot": False, "first_name": "Test"}
    chat = {"id": 42, "type": "private"}

    async def feed(**kwargs):
        update = Update.model_validate(
            {"update_id": 123, **kwargs}, context={"bot": bot}
        )
        await dp.feed_update(bot, update)

    await feed(
        message={
            "message_id": 1,
            "chat": chat,
            "from": actor,
            "date": 1780000000,
            "text": "/help",
        }
    )
    assert transport.calls[-1].__api_method__ == "sendWidget"
    mid = len(transport.calls)

    def action(name):
        with sessions() as db:
            row = db.get(PrivateMenu, (42, mid))
            for p, page in enumerate(json.loads(row.content)):
                for i, (_, callback) in enumerate(page["buttons"]):
                    if callback == name:
                        return f"m:{row.revision}:{p}:{i}"
        raise AssertionError(name)

    async def tap(data, uid=42):
        await feed(
            widget_query={
                "id": "query1",
                "chat": chat,
                "message_id": mid,
                "from": {**actor, "id": uid},
                "data": data,
            }
        )
        result = transport.calls[-1]
        assert result.__api_method__ == "answerWidgetQuery"
        return json.loads(result.data)

    settings = action("help:settings:42")
    assert "msg" in await tap(settings, uid=43)
    await tap(settings)
    await tap(action("help:notifications:42"))
    toggle = action("help:notifications_toggle:42")
    await tap(toggle)
    await tap(toggle)
    with sessions() as db:
        assert not db.get(Chat, 42).notifications_enabled
    await tap(action("help:settings:42"))
    await tap(action("help:delete_me:42"))
    confirm = action("help:delete_confirm_db:42")
    await tap(action("help:settings:42"))
    await tap(confirm)
    await tap(action("help:back:42"))
    await tap(action("stats:back:root"))
    await tap(action("stats:open:my"))
    assert action("stats:period:my:week:0")
    # The same callbacks inside the journal must never publish menu state over the daily ledger.
    from app.db.models import Session as DaySession
    from app.services.group_daily_widget import send as send_question
    from app.services.repo_service import set_session_message_id

    with sessions.begin() as db:
        sess = DaySession(chat_id=42, status="active", session_date=datetime.now(UTC).date())
        db.add(sess)
        db.flush()
        question = await send_question(bot, db, sess)
        mid = question.message_id
        set_session_message_id(db, sess.session_id, "Q1", mid)
    start = len(transport.calls)

    async def journal(key, **kw):
        result = await tap("q:j:" + key, **kw)
        assert len(encode(result).encode()) <= 1024
        return result

    def button(page, name):
        return next(key for label, key in page["nav"] if name in label)

    assert "t" in await journal("cmd.help", uid=43)
    help_page = await journal("cmd.help")
    settings_page = await journal(button(help_page, "Настройки"))
    notify = await journal(button(settings_page, "Уведомления"))
    toggle = button(notify, "Уведомления:")
    await journal(toggle)
    await journal(toggle)
    with sessions() as db:
        assert db.get(Chat, 42).notifications_enabled
    help_page = await journal("cmd.help")
    about = await journal(button(help_page, "О боте"))
    assert button(about, "GitHub").startswith("https://github.com/")
    assert "Настройки" in str((await journal(about["up"]))["nav"])
    stats = await journal("cmd.stats")
    for scope in ("Моя", "В этой личке", "Среди чатов", "Глобальная"):
        assert button(stats, scope)
    mine = await journal(button(stats, "Моя"))
    week = await journal(button(mine, "Неделя"))
    assert "Архив" in week["title"]
    await journal(button(week, "Раньше"))
    assert all(call.__api_method__ not in ("sendWidget", "sendMessage", "editMessageText", "editMessageWidgetData") for call in transport.calls[start:])
    # A group goes directly to the normal Telegram transport.
    await bot.send_message(
        -42, "Stats", reply_markup=commands.stats_root_kb(False, False)
    )
    assert transport.calls[-1].__api_method__ == "sendMessage"


def test_long_pages_keep_all_actions_and_fit_telegram():
    buttons = [[f"Группа {i} " + "🐈" * 40, f"recap:pick:chat:{i}"] for i in range(24)]
    pages = make_pages("Итоги\n" + "длинная строка\n" * 120, buttons)
    assert [item for p in pages for item in p["buttons"]] == buttons
    for i in range(len(pages)):
        assert len(encode(page_state(pages, "f" * 16, i)).encode()) <= 1024
