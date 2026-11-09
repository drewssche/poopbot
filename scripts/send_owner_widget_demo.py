"""Send one native demo widget to BOT_OWNER_ID from the configured bot."""
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from app.core.config import load_settings
from app.db.runtime import configure_database, close_database
from app.db.session import db_session
from app.services.app_setting_service import set_setting
from app.services.daily_widget_demo import KEY, SendWidget, public_state, render_demo


async def main():
    settings = load_settings()
    if not settings.bot_owner_id or settings.bot_owner_id <= 0:
        raise SystemExit('Private BOT_OWNER_ID required')
    sessions = configure_database(settings)
    state = {'n': 1, 'r': 0}
    try:
        async with Bot(settings.bot_token) as bot:
            message = await bot(SendWidget(chat_id=settings.bot_owner_id,
                html=render_demo(datetime.now(ZoneInfo('Europe/Minsk')).date()), chat_data=public_state(state)))
            if message.chat.type != 'private' or message.chat.id != settings.bot_owner_id or not getattr(message, 'widget', None):
                raise RuntimeError('Unexpected Telegram response; do not resend automatically')
            state['message'] = message.message_id
            with db_session(sessions) as db:
                set_setting(db, KEY, json.dumps(state))
            print('Delivered private widget:', message.message_id)
    finally:
        close_database()


if __name__ == '__main__':
    asyncio.run(main())
