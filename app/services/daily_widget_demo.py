"""Private, owner-only widget pilot. Has no dependency on the event ledger."""
import asyncio
import base64
import json
import re
import zlib
from datetime import timedelta
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods.base import TelegramMethod
from aiogram.types import Message

from app.db.models import AppSetting
from app.db.session import db_session

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'app/assets/widgets'
KEY = 'daily_widget_demo_owner_v1'
DAYS = 'пн вт ср чт пт сб вс'.split()
MONTHS = 'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()


class SendWidget(TelegramMethod[Message]):
    __api_method__ = 'sendWidget'
    __returning__ = Message
    chat_id: int
    html: str
    chat_data: str
    allow_queries: bool = True


class EditWidget(TelegramMethod[bool | Message]):
    __api_method__ = 'editMessageWidgetData'
    __returning__ = bool | Message
    chat_id: int
    message_id: int
    chat_data: str


class AnswerWidget(TelegramMethod[bool]):
    __api_method__ = 'answerWidgetQuery'
    __returning__ = bool
    widget_query_id: str
    data: str | None = None


def render_demo(day):
    start = day - timedelta(days=day.weekday())
    icon = (ROOT / 'web/demo/assets/poop-day.svg').read_text().strip()
    cells = []
    for i, name in enumerate(DAYS):
        d = start + timedelta(days=i)
        current = d == day
        n = 1 if current else (2, 0, 1, 3, 1, 0, 2)[i] if d < day else 0
        box_id, badge_id = (' id="now"', ' id="badge"') if current else ('', '')
        cells.append(f'<div class="cell{" today" if current else ""}">{name}'
                     f'<span{box_id} class="dot {"on" if n else "off"}">{icon}<b>{d.day}</b>'
                     f'<i{badge_id}>{n}</i></span></div>')
    logo = 'data:image/png;base64,' + base64.b64encode((ASSETS / 'logo-180.png').read_bytes()).decode()
    html = ((ASSETS / 'daily-demo.html').read_text().replace('__LOGO__', logo)
            .replace('__DATE__', f'{day.day} {MONTHS[day.month-1]}').replace('__WEEK__', ''.join(cells)))
    if len(html.encode()) > 32768 or len(zlib.compress(html.encode(), 9)) > 4050:
        raise ValueError('Daily widget exceeds Telegram size budget')
    return html


def public_state(state):
    return json.dumps({k: state[k] for k in ('n', 'r')}, separators=(',', ':'))


class DemoQueries:
    def __init__(self, owner, sessions):
        self.owner, self.sessions = owner, sessions
        self.lock = asyncio.Lock()

    async def __call__(self, handler, event, data):
        q = (event.model_extra or {}).get('widget_query')
        if q is None:
            return await handler(event, data)
        bot = data['bot']
        cmd = re.fullmatch(r'demo:(\d{1,12}):([+-])', str(q.get('data', '')))
        chat = q.get('chat') or {}
        if not (cmd and self.owner and chat.get('type') == 'private' and
                chat.get('id') == self.owner == q.get('from', {}).get('id')):
            return await bot(AnswerWidget(widget_query_id=q['id']))
        async with self.lock:
            with db_session(self.sessions) as db:
                row = db.get(AppSetting, KEY, with_for_update=True)
                s = json.loads(row.value) if row else {}
                if s.get('message') != q.get('message_id') or not s:
                    reply = None
                else:
                    if s['r'] == int(cmd[1]):
                        s.update(n=max(0, min(99, s['n'] + (1 if cmd[2] == '+' else -1))), r=s['r'] + 1)
                        row.value = json.dumps(s)
                    reply = public_state(s)
            if reply:
                try:
                    await bot(EditWidget(chat_id=self.owner, message_id=s['message'], chat_data=reply))
                except TelegramBadRequest as exc:
                    if 'not modified' not in exc.message.lower():
                        raise
            return await bot(AnswerWidget(widget_query_id=q['id'], data=reply))
