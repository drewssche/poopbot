"""Publish text questions; keep previously sent photo questions editable."""
from app.services.q1_card_state import queue_question_view
from app.services.scheduler_telegram import safe_edit_message_text


async def edit_question(bot, db, *, chat_id, message_id, text, reply_markup=None, **kwargs):
    if queue_question_view(db, chat_id, message_id, text, reply_markup):
        return None
    return await safe_edit_message_text(bot, chat_id=chat_id, message_id=message_id,
                                       text=text, reply_markup=reply_markup, **kwargs)


async def send_question(bot, db, *, chat_id, session_id, text, reply_markup=None):
    # All entry points (scheduler, /start, private flow) use the same transport.
    # Existing photo questions keep their outbox rows and their original IDs.
    return await bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML',
                                  reply_markup=reply_markup, request_timeout=20)
