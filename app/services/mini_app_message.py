"""Refresh an existing Q1 after a committed mark; never create unsolicited posts."""
from datetime import timedelta
from sqlalchemy import select
from app.db.models import Chat, Session as DaySession
from app.services.repo_service import get_session_message_id
from app.services.q1_service import render_q1, render_q1_private, should_show_restore_streak_button
from app.services.q1_markup import q1_markup
from app.services.time_service import get_session_window


def current_message(db, chat_id, user_id, changed_date=None):
    chat = db.get(Chat, chat_id)
    window = get_session_window(chat.timezone)
    if window.is_blocked_window:
        return None
    sess = db.scalar(select(DaySession).where(DaySession.chat_id == chat_id, DaySession.session_date == (changed_date or window.session_date)))
    if not sess:
        return None
    mid = get_session_message_id(db, sess.session_id, 'Q1')
    if not mid:
        return None
    private = chat_id > 0
    closed = sess.status == 'closed'
    if not closed and sess.session_date != window.session_date:
        return None
    text = (render_q1_private(db, chat_id, sess.session_id, user_id, sess.session_date, closed=closed) if private
            else render_q1(db, chat_id, sess.session_id, sess.session_date, closed=closed))
    if closed:
        from app.services.restore_period_service import can_mark_closed_session
        can_mark = can_mark_closed_session(sess, window.session_date)
        text = '🔒 Сессия закрыта.\n\n' + text
        if can_mark:
            text += f"\n\nЗабыл отметиться? Можно добавить одну отметку за {sess.session_date:%d.%m} до конца {(sess.session_date + timedelta(days=1)):%d.%m}."
        return {'chat_id': chat_id, 'message_id': mid, 'text': text, 'parse_mode': 'HTML',
                'reply_markup': {'inline_keyboard': [[{'text': '💩 Забыл отметиться', 'callback_data': 'q1:forgot'}]] if can_mark else []}}
    restore = should_show_restore_streak_button(db, chat_id=chat_id, session_date=sess.session_date,
                                               viewer_user_id=user_id if private else None, is_private_chat=private)
    show_questions = (not private and not chat.q2_q3_enabled
                      and not get_session_message_id(db, sess.session_id, 'Q2')
                      and not get_session_message_id(db, sess.session_id, 'Q3'))
    return {'chat_id': chat_id, 'message_id': mid, 'text': text, 'parse_mode': 'HTML',
            'reply_markup': q1_markup(True, show_questions, restore)}
