"""Launch from a private WebApp button or a group-scoped Telegram link."""
import os
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from app.core.config import load_settings
from app.db.runtime import get_session_factory
from app.db.session import db_session
from app.services.repo_service import upsert_chat
from app.services.mini_app_links import group_scope

router = Router()


@router.message(Command('app'))
async def app_cmd(message: Message):
    if not message.from_user or message.chat.type not in {'private', 'group', 'supergroup'}:
        return
    settings = load_settings()
    origin = os.environ.get('MINI_APP_ORIGIN', '').rstrip('/')
    if not origin.startswith('https://'):
        await message.answer('Приложение пока не настроено.')
        return
    if message.chat.type == 'private':
        button = InlineKeyboardButton(text='Открыть приложение', web_app=WebAppInfo(url=origin + '/poopcheck/'))
        text = 'Твои личные отметки и статистика.'
    else:
        with db_session(get_session_factory(settings.database_url)) as db:
            upsert_chat(db, message.chat.id)
        me = await message.bot.get_me()
        scope = group_scope(settings.bot_token, message.chat.id)
        button = InlineKeyboardButton(text='Открыть приложение', url=f'https://t.me/{me.username}?startapp={scope}')
        from html import escape
        text = f'Твои отметки и статистика в «{escape(message.chat.title or "этой группе")}».'
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))
