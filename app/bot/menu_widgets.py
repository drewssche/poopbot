"""Route private menu presses through the same authorized callbacks as inline buttons."""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import re
from secrets import token_hex

from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy import delete, update

from app.db.models import PrivateMenu, WeeklyReport
from app.services.daily_widget_demo import AnswerWidget, EditWidget, SendWidget
from app.services.group_daily_widget import JOURNAL_PREFIX, enabled, question_query
from app.services.menu_widget import (
    encode,
    journal_menu_page,
    layout,
    make_pages,
    page_state,
    scheduled_widget_notice,
)

logger = logging.getLogger(__name__)
PREFIXES = (
    "help:",
    "stats:",
    "recap:",
    "debug:",
    "streakadmin:",
    "restore:",
    "restorepreview:",
)
QUERY = re.compile(r"m:([0-9a-f]{16}):(\d{1,5})(?::(\d{1,2}))?")


@dataclass
class Press:
    address: tuple
    query_id: str
    pages: list
    notice: str = ""
    embedded: bool = False


active_press = ContextVar("private_menu_press", default=None)


def new_revision():
    return token_hex(8)


class MenuWidgets:
    def __init__(self, sessions, dispatcher):
        self.sessions = sessions
        self.dispatcher = dispatcher

    def recover_interrupted_presses(self):
        # A terminated polling process cannot finish its callbacks. Discard the
        # spent button tokens, allowing a fresh view without replaying actions.
        with self.sessions.begin() as db:
            db.execute(
                update(PrivateMenu)
                .where(PrivateMenu.revision.startswith("!"))
                .values(revision=new_revision())
            )

    async def buttons(self, bot, markup):
        if markup is None:
            return []
        if not isinstance(markup, InlineKeyboardMarkup):
            return None
        result = []
        for row in markup.inline_keyboard:
            for button in row:
                action = button.callback_data
                if button.web_app:
                    action = f"https://t.me/{(await bot.me()).username}?startapp"
                elif button.url and button.url.startswith("https://"):
                    action = button.url
                elif not action or not action.startswith(PREFIXES):
                    return None
                result.append([button.text, action])
        return result

    async def pages(self, bot, text, buttons, *, embedded=False):
        if text.startswith("Помощь\n") and any(
            a.startswith("help:about:") for _, a in buttons
        ):
            buttons = [
                *buttons,
                [
                    "Открыть приложение",
                    f"https://t.me/{(await bot.me()).username}?startapp",
                ],
                ["Статистика", "stats:back:root"],
            ]
        return make_pages(text, buttons, embedded=embedded)

    async def outgoing(self, make_request, bot, method):
        press = active_press.get()
        if (
            isinstance(method, AnswerCallbackQuery)
            and press
            and method.callback_query_id == press.query_id
        ):
            if method.text:
                press.notice = method.text
            return True
        chat_id = getattr(method, "chat_id", None)
        if type(chat_id) is not int or chat_id <= 0:
            return await make_request(bot, method)

        if isinstance(method, SendMessage) and (
            method.reply_markup
            or scheduled_widget_notice.get()
            or (press and chat_id == press.address[0])
        ):
            with self.sessions() as db:
                use_widget = enabled(db, chat_id)
            if use_widget:
                buttons = await self.buttons(bot, method.reply_markup)
                if buttons is not None:
                    pages = await self.pages(bot, method.text, buttons)
                    revision = new_revision()
                    options = {}
                    if method.reply_parameters:
                        options["reply_parameters"] = method.reply_parameters
                    elif method.reply_to_message_id:
                        options["reply_parameters"] = {
                            "message_id": method.reply_to_message_id
                        }
                    sent = await bot(
                        SendWidget(
                            chat_id=chat_id,
                            html=layout(),
                            chat_data=encode(page_state(pages, revision)),
                            **options,
                        )
                    )
                    with self.sessions.begin() as db:
                        db.add(
                            PrivateMenu(
                                chat_id=chat_id,
                                message_id=sent.message_id,
                                revision=revision,
                                content=encode(pages),
                            )
                        )
                    return sent

        if isinstance(method, DeleteMessage):
            result = await make_request(bot, method)
            with self.sessions.begin() as db:
                db.execute(
                    delete(PrivateMenu).where(
                        PrivateMenu.chat_id == chat_id,
                        PrivateMenu.message_id == method.message_id,
                    )
                )
            return result

        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            address = (chat_id, method.message_id)
            if press and press.embedded and address == press.address:
                buttons = await self.buttons(bot, method.reply_markup)
                if buttons is None or not isinstance(method, EditMessageText):
                    raise ValueError("Unsupported journal menu edit")
                press.pages = await self.pages(bot, method.text, buttons, embedded=True)
                return True
            with self.sessions() as db:
                row = db.get(PrivateMenu, address)
                old = json.loads(row.content) if row else None
            if old:
                buttons = await self.buttons(bot, method.reply_markup)
                if buttons is None:
                    raise ValueError("Unsupported private menu button")
                text = (
                    method.text
                    if isinstance(method, EditMessageText)
                    else old[0]["t"] + "\n" + "\n".join(p["b"] for p in old)
                )
                pages = await self.pages(bot, text, buttons)
                if press and address == press.address:
                    press.pages = pages
                    return True
                revision = new_revision()
                with self.sessions.begin() as db:
                    row = db.get(PrivateMenu, address, with_for_update=True)
                    if row:
                        row.revision, row.content = revision, encode(pages)
                return await bot(
                    EditWidget(
                        chat_id=chat_id,
                        message_id=address[1],
                        chat_data=encode(page_state(pages, revision)),
                    )
                )
        return await make_request(bot, method)

    async def incoming(self, handler, event, data):
        q = (event.model_extra or {}).get("widget_query") or {}
        raw = q.get("data")
        if not isinstance(raw, str):
            return await handler(event, data)
        embedded = raw.startswith(("q:j:cmd.", "q:j:m:"))
        if not embedded and not raw.startswith("m:"):
            return await handler(event, data)
        route = raw[4:] if embedded else raw
        match = QUERY.fullmatch(route)
        root = route if route in ("cmd.help", "cmd.stats") else None
        bot = data["bot"]
        chat, actor = q.get("chat") or {}, q.get("from") or {}
        address = (chat.get("id"), q.get("message_id"))
        unavailable = {"msg": "Открой меню заново в личке с ботом."}
        answer = unavailable
        if (
            (match or root)
            and chat.get("type") == "private"
            and type(address[0]) is int and address[0] > 0
            and actor.get("id") == address[0] and not actor.get("is_bot")
            and type(address[1]) is int and address[1] > 0
        ):
            answer = await self.handle(bot, address, q, match, unavailable, embedded=embedded, root=root)
        if embedded and "msg" in answer and "title" not in answer:
            answer = {"t": answer["msg"]}
        return await bot(AnswerWidget(widget_query_id=q["id"], data=encode(answer)))

    async def handle(self, bot, address, q, match, unavailable, *, embedded=False, root=None):
        # Embedded menus have separate state so menu refreshes cannot replace Q1 chat_data.
        storage = (address[0], -address[1]) if embedded else address
        render = journal_menu_page if embedded else page_state
        with self.sessions.begin() as db:
            if not enabled(db, address[0]):
                return unavailable
            if embedded and db.scalar(question_query(*address).where(
                WeeklyReport.delivery_key.startswith(JOURNAL_PREFIX)
            ).with_for_update(of=WeeklyReport)) is None:
                return unavailable
            row = db.get(PrivateMenu, storage, with_for_update=True)
            if row is None:
                if not root:
                    return unavailable
                row = PrivateMenu(chat_id=storage[0], message_id=storage[1], revision=new_revision(), content="[]")
                db.add(row)
            pages = json.loads(row.content)
            if row.revision.startswith("!"):
                return {
                    "msg": "Действие выполняется. Если ответ не пришёл, открой команду снова."
                }
            if root:
                action = f"help:back:{address[0]}" if root == "cmd.help" else "stats:back:root"
            else:
                revision, page, index = match.groups()
                page = int(page)
                if revision != row.revision or page >= len(pages):
                    return render(pages, row.revision) if pages else unavailable
                if index is None:
                    return render(pages, revision, page)
                buttons = pages[page]["buttons"]
                if int(index) >= len(buttons):
                    return unavailable
                action = buttons[int(index)][1]
                if not action.startswith(PREFIXES):
                    return unavailable
            # Consume this screen before invoking an action (including toggles and broadcasts).
            claimed = row.revision = "!" + token_hex(7)

        press = Press(address, q["id"], pages, embedded=embedded)
        token = active_press.set(press)
        try:
            cb = CallbackQuery.model_validate(
                dict(
                    id=q["id"],
                    chat_instance=str(address[0]),
                    data=action,
                    **{"from": q["from"]},
                    message=dict(
                        date=int(datetime.now(UTC).timestamp()),
                        message_id=address[1],
                        chat=q["chat"],
                    ),
                ),
                context={"bot": bot},
            )
            await self.dispatcher.propagate_event("callback_query", cb, bot=bot)
        except Exception:
            logger.exception("Private menu callback failed")
            press.notice = "Не удалось выполнить действие. Проверь текущие данные."
        finally:
            active_press.reset(token)

        with self.sessions.begin() as db:
            row = db.get(PrivateMenu, storage, with_for_update=True)
            if not row:
                if embedded and press.notice and action.startswith("help:delete_confirm_db:"):
                    return {"title": press.notice, "active": "command", "nav": []}
                return {"msg": press.notice} if press.notice else unavailable
            if row.revision != claimed:
                return render(json.loads(row.content), row.revision)
            row.content = encode(press.pages)
            row.revision = new_revision()
            state = render(press.pages, row.revision) if press.pages else unavailable
        if not embedded:
            await bot(EditWidget(chat_id=address[0], message_id=address[1], chat_data=encode(state)))
        if press.notice:
            with_notice = {**state, "msg": press.notice}
            return (
                with_notice
                if len(encode(with_notice).encode()) <= 1024
                else {"msg": press.notice[:180]}
            )
        return state
