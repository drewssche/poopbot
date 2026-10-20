"""Refresh existing session messages after a committed mark; never send new posts."""
from datetime import timedelta
from sqlalchemy import select
from app.db.models import Chat, Session as DaySession
from app.services.repo_service import get_session_message_id
from app.services.q1_service import render_q1, render_q1_private, should_show_restore_streak_button
from app.services.q1_markup import q1_markup
from app.services.time_service import get_session_window
from app.services.q2_q3_render import render_q2_text, render_q3_text
from app.services.q2_q3_markup import q2_markup, q3_markup


def current_messages(db, chat_id, user_id, changed_date=None):
    q1 = current_message(db, chat_id, user_id, changed_date)
    payloads = [q1] if q1 else []
    # Personal questions reuse Q1. Return that message to the current overview,
    # never replace it again with a group question or stale event-number buttons.
    if chat_id > 0:
        return payloads
    chat = db.get(Chat, chat_id)
    if not chat:
        return payloads
    window = get_session_window(chat.timezone)
    if window.is_blocked_window:
        return payloads
    sess = db.scalar(select(DaySession).where(
        DaySession.chat_id == chat_id,
        DaySession.session_date == (changed_date or window.session_date)))
    if not sess or (sess.status != 'closed' and sess.session_date != window.session_date):
        return payloads
    for kind, render, markup in [('Q2', render_q2_text, q2_markup), ('Q3', render_q3_text, q3_markup)]:
        mid = get_session_message_id(db, sess.session_id, kind)
        if not mid or any(p['message_id'] == mid for p in payloads):
            continue
        closed = sess.status == 'closed'
        text = render(db, chat_id, sess.session_id)
        payloads.append({'chat_id': chat_id, 'message_id': mid,
                         'text': ('🔒 Сессия закрыта.\n\n' if closed else '') + text,
                         'parse_mode': 'HTML',
                         'reply_markup': {'inline_keyboard': []} if closed else markup()})
    return payloads


def current_message(db, chat_id, user_id, changed_date=None):
    chat = db.get(Chat, chat_id)
    if not chat:
        return None
    window = get_session_window(chat.timezone)
    if window.is_blocked_window:
        return None
    sess = db.scalar(select(DaySession).where(DaySession.chat_id == chat_id, DaySession.session_date == (changed_date or window.session_date)))
    if not sess:
        return None
    if sess.status != 'closed' and sess.session_date != window.session_date:
        return None
    return session_question(db, chat, sess, user_id, window.session_date)


def session_question(db, chat, sess, user_id, today):
    """Render a known session, also usable during the daily rollover."""
    chat_id = chat.chat_id
    mid = get_session_message_id(db, sess.session_id, 'Q1')
    if not mid:
        return None
    private = chat_id > 0
    closed = sess.status == 'closed'
    text = (render_q1_private(db, chat_id, sess.session_id, user_id, sess.session_date, closed=closed) if private
            else render_q1(db, chat_id, sess.session_id, sess.session_date, closed=closed))
    if closed:
        from app.services.restore_period_service import can_mark_closed_session
        can_mark = can_mark_closed_session(sess, today)
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
