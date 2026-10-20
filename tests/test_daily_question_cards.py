import asyncio
from datetime import date, timedelta, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.models import Chat, User, ChatMember, PoopEvent, Session as DaySession, SessionMessage, WeeklyReport
from app.services.q1_card_service import edit_question
from app.services.q1_card_state import question_caption, units, queue_photo_questions, plain_text
from app.services.scheduler_service import _post_q1, _lock_q1
from app.services.weekly_reports import ReportWorker
from app.services.mini_app_message import current_messages
from app.bot.keyboards.q2 import q2_keyboard
from app.bot.keyboards.q1 import q1_keyboard
from app.services.time_service import SessionWindow

DAY = date(2026, 10, 20)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/daily.db')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    bot = SimpleNamespace(**{name: AsyncMock() for name in (
        'send_photo', 'send_message', 'edit_message_media', 'edit_message_caption', 'edit_message_text')})
    bot.send_photo.side_effect = [SimpleNamespace(message_id=n) for n in range(100, 120)]
    bot.get_chat = AsyncMock(return_value=SimpleNamespace(title='Тестовый чат'))
    with factory.begin() as db:
        db.add_all([Chat(chat_id=-1), Chat(chat_id=1), User(user_id=1, first_name='Аня')])
        db.flush()
        db.add(ChatMember(chat_id=-1, user_id=1))
    monkeypatch.setattr('app.services.time_service.now_in_tz', lambda _: datetime(2026, 10, 21, 12))
    monkeypatch.setattr('app.services.scheduler_service.now_in_tz', lambda _: datetime(2026, 10, 21, 12))
    monkeypatch.setattr('app.services.mini_app_message.get_session_window', lambda _: SessionWindow(DAY, False, True))
    yield factory, bot, ReportWorker(bot, factory, 'unused')
    engine.dispose()


def post(factory, bot, chat=-1, day=DAY):
    with factory.begin() as db:
        sess = DaySession(chat_id=chat, session_date=day, status='active')
        db.add(sess); db.flush()
        asyncio.run(_post_q1(bot, db, chat, sess.session_id, day, False))
        db.flush()
        row = db.scalar(select(WeeklyReport).where(WeeklyReport.q1_session_id == sess.session_id))
        assert db.get(SessionMessage, (sess.session_id, 'Q1')).message_id == row.photo_message_id
        return row.id, sess.session_id, row.photo_message_id


@pytest.mark.parametrize('chat', [-1, 1])
def test_new_days_get_separate_photos_and_roll_over_week(env, chat):
    factory, bot, worker = env
    ids = [post(factory, bot, chat, day) for day in (DAY, DAY+timedelta(days=1), DAY+timedelta(days=6))]
    assert len({mid for _, _, mid in ids}) == 3
    assert [worker.get(rid).period_start for rid, _, _ in ids] == [
        date(2026,10,19), date(2026,10,19), date(2026,10,26)]
    assert bot.send_photo.await_count == 3
    bot.send_message.assert_not_awaited()
    for call in bot.send_photo.await_args_list:
        assert call.kwargs['reply_markup']
        assert '21.10.26' in call.kwargs['caption'] or '20.10.26' in call.kwargs['caption'] or '26.10.26' in call.kwargs['caption']
        assert units(call.kwargs['caption']) <= 1024


def test_same_photo_refreshes_marks_and_closed_buttons(env):
    factory, bot, worker = env
    rid, sid, mid = post(factory, bot)
    with factory.begin() as db:
        db.add(PoopEvent(session_id=sid, user_id=1, origin_chat_id=-1, event_n=1))
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    media = bot.edit_message_media.await_args.kwargs
    assert media['message_id'] == mid and media['media'].parse_mode == 'HTML'
    assert 'Кто сегодня какал?' in media['media'].caption
    assert any(b.callback_data == 'q1:plus' for row in media['reply_markup'].inline_keyboard for b in row)
    with factory.begin() as db:
        db.get(DaySession, sid).status = 'closed'
        asyncio.run(_lock_q1(bot, db, -1, sid))
    asyncio.run(worker.deliver(rid))
    payload = bot.edit_message_caption.await_args.kwargs
    assert 'Сессия закрыта' in payload['caption']
    assert payload['reply_markup'].inline_keyboard[0][0].callback_data == 'q1:forgot'
    assert bot.send_photo.await_count == 1
    bot.edit_message_text.assert_not_awaited()


def test_private_questions_survive_card_refresh_then_return_to_overview(env):
    factory, bot, worker = env
    rid, sid, mid = post(factory, bot, 1)
    with factory.begin() as db:
        asyncio.run(edit_question(bot, db, chat_id=1, message_id=mid, text='Консистенция?',
                                  reply_markup=q2_keyboard(private_flow=True, event_n=1)))
    asyncio.run(worker.deliver(rid))
    assert bot.edit_message_caption.await_args.kwargs['caption'] == 'Консистенция?'
    with factory.begin() as db:
        db.add(PoopEvent(session_id=sid, user_id=1, origin_chat_id=1, event_n=1))
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    payload = bot.edit_message_media.await_args.kwargs
    assert payload['media'].caption == 'Консистенция?'
    assert payload['reply_markup'].inline_keyboard[0][0].callback_data.startswith('q2:set:')
    with factory.begin() as db:
        # Mini App returns the personal message to Q1, via the same durable publisher.
        assert queue_photo_questions(db, current_messages(db, 1, 1)) == []
    asyncio.run(worker.deliver(rid))
    assert 'Твоя личная сессия' in bot.edit_message_caption.await_args.kwargs['caption']
    assert worker.get(rid).q1_flow_text is None


def test_legacy_text_messages_keep_their_transport(env):
    factory, bot, _ = env
    with factory.begin() as db:
        asyncio.run(edit_question(bot, db, chat_id=-1, message_id=999, text='Текст',
                                  reply_markup=q1_keyboard(True)))
        payloads = [{'chat_id': -1, 'message_id': 999, 'text': 'Текст'}]
        assert queue_photo_questions(db, payloads) == payloads
    bot.edit_message_text.assert_awaited_once()
    bot.send_photo.assert_not_awaited()


def test_large_caption_preserves_mentions_and_handles_unicode():
    text = '💩 Кто сегодня какал?\nУчастники:\n' + '\n'.join(
        f'<a href="tg://user?id={n}">Участник 💩 &amp; друг {n}</a> — 💩(123) | стрик 365 дн.' for n in range(100))
    caption = question_caption(text)
    assert units(caption) <= 1024
    assert caption.count('<a ') == caption.count('</a>')
    assert caption.endswith('Полный список и отметки: /app')
    assert 'Участник 💩 & друг 0' in plain_text(caption)
    assert units(question_caption('<b>' + '💩'*2000 + '</b>')) <= 1024


def test_seven_daily_messages_share_one_render_per_visual_change(env, monkeypatch):
    from unittest.mock import Mock
    from app.services import report_image_cache as cache
    cache.clear_image_cache()
    render = Mock(wraps=cache.renderer.render_card)
    monkeypatch.setattr(cache.renderer, 'render_card', render)
    factory, bot, worker = env
    monday = DAY - timedelta(days=DAY.weekday())
    posts = [post(factory, bot, day=monday+timedelta(days=n)) for n in range(7)]
    assert render.call_count == 1
    with factory.begin() as db:
        db.add(PoopEvent(session_id=posts[0][1], user_id=1, origin_chat_id=-1, event_n=1))
        for rid, _, _ in posts:
            db.get(WeeklyReport, rid).revision += 1
    for rid, _, _ in posts:
        asyncio.run(worker.deliver(rid))
    assert render.call_count == 2
    assert bot.edit_message_media.await_count == 7
    calls = [c.kwargs for c in bot.edit_message_media.await_args_list]
    assert len({c['media'].media.data for c in calls}) == 1
    assert len({c['media'].caption for c in calls}) == 7
    assert {c['message_id'] for c in calls} == {mid for _, _, mid in posts}
    assert all(worker.get(rid).revision == worker.get(rid).delivered_revision for rid, _, _ in posts)


def test_private_caption_change_skips_render_after_cache_loss(env, monkeypatch):
    from unittest.mock import Mock
    from app.services import report_image_cache as cache
    factory, bot, worker = env
    rid, _, mid = post(factory, bot, 1)
    cache.clear_image_cache()
    render = Mock(side_effect=AssertionError('Caption-only refresh must not render'))
    monkeypatch.setattr(cache.renderer, 'render_card', render)
    with factory.begin() as db:
        asyncio.run(edit_question(bot, db, chat_id=1, message_id=mid, text='Самочувствие?',
                                  reply_markup=q2_keyboard(private_flow=True, event_n=1)))
    worker = ReportWorker(bot, factory, 'unused')
    asyncio.run(worker.deliver(rid))
    render.assert_not_called()
    bot.edit_message_media.assert_not_awaited()
    assert bot.edit_message_caption.await_args.kwargs['caption'] == 'Самочувствие?'
    assert worker.get(rid).revision == worker.get(rid).delivered_revision
