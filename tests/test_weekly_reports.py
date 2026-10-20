import asyncio
from datetime import date, timedelta
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageMedia, SendPhoto
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Chat, PoopEvent, Session as DaySession, User, WeeklyReport
from app.services.weekly_report_card import Participant, WeeklyCard, project_card, render_card, period_text
from app.services.weekly_reports import WeeklyReportWorker, enqueue_weekly_report, MAX_ATTEMPTS

START = date(2026, 10, 12)
END = START + timedelta(days=6)


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/reports.db')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as db:
        db.add_all([Chat(chat_id=-1), Chat(chat_id=-2), Chat(chat_id=1),
                    User(user_id=1, first_name='Аня'), User(user_id=2, first_name='Борис')])
    bot = SimpleNamespace(**{name: AsyncMock(return_value=SimpleNamespace(message_id=10 if name == 'send_photo' else 11))
        for name in ('send_photo', 'send_message', 'edit_message_media', 'edit_message_text', 'edit_message_caption', 'delete_message', 'get_chat')})
    worker = WeeklyReportWorker(bot, factory, 'unused')
    yield factory, bot, worker
    engine.dispose()


def mark(db, chat=-1, day=START, uid=1, origin=None, n=1):
    session = db.scalar(select(DaySession).where(DaySession.chat_id == chat, DaySession.session_date == day))
    if not session:
        session = DaySession(chat_id=chat, session_date=day)
        db.add(session); db.flush()
    event = PoopEvent(session_id=session.session_id, user_id=uid, origin_chat_id=chat if origin is None else origin,
                     event_n=n, bristol=3, feeling='ok')
    db.add(event); db.flush()
    return event


def queued(factory):
    with factory.begin() as db:
        return enqueue_weekly_report(db, -1, END, scheduled=True, chat_title='Тестовый чат').id


def test_projection_excludes_other_scope_and_outside_period(env):
    factory, _, _ = env
    with factory.begin() as db:
        mark(db); mark(db, n=2); mark(db, uid=2)
        mark(db, day=START-timedelta(days=1)); mark(db, day=END+timedelta(days=1))
        mark(db, chat=-2); mark(db, uid=2, n=2, origin=-2)
        card = project_card(db, -1, START, END, 'Чат')
        assert card.days == (3, 0, 0, 0, 0, 0, 0)
        assert [(p.name, p.count) for p in card.participants] == [('Аня', 2), ('Борис', 1)]
        mark(db, chat=1); mark(db, chat=1, uid=2)
        assert project_card(db, 1, START, END, 'Личные').total == 1


@pytest.mark.parametrize('counts,names', [((0,)*7, []), ((1,0,0,0,0,0,0), ['Аня']),
    ((1234,12,3,4,5,6,7), ['Очень длинное имя ' * 8, '💩🚀', 'Я & <ты>'] + ['Участник']*20)])
def test_render_edge_cases_deterministic(counts, names):
    card = WeeklyCard(START, END, 'Длинное название '*30, counts, tuple(Participant(n, 1234) for n in names))
    picture = render_card(card)
    assert picture == render_card(card)
    image = Image.open(BytesIO(picture))
    assert image.width == 840 and 850 < image.height < 1500
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_cross_year_period_is_explicit():
    assert period_text(date(2026, 12, 28), date(2027, 1, 3)) == '28 декабря 2026 – 3 января 2027'


def test_scheduled_queue_is_idempotent(env):
    factory, _, _ = env
    assert queued(factory) == queued(factory)
    with factory() as db:
        assert len(db.scalars(select(WeeklyReport)).all()) == 1


def test_delivery_refresh_and_repeated_revision_are_in_place(env):
    factory, bot, worker = env
    rid = queued(factory)
    asyncio.run(worker.deliver(rid))
    bot.send_photo.assert_awaited_once(); bot.send_message.assert_not_awaited()
    with factory.begin() as db:
        mark(db)
        db.get(WeeklyReport, rid).revision += 1  # Production uses database trigger.
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_awaited_once(); bot.edit_message_text.assert_not_awaited()
    assert bot.edit_message_media.await_args.kwargs['message_id'] == 10
    assert bot.edit_message_media.await_args.kwargs['media'].caption
    with factory.begin() as db:
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    assert bot.edit_message_media.await_count == 1 and bot.edit_message_caption.await_count == 0
    bot.send_photo.assert_awaited_once(); bot.send_message.assert_not_awaited()
    row = worker.get(rid)
    assert row.delivered_revision == row.revision


def test_quality_edit_updates_only_text_and_delete_clears_card(env):
    factory, bot, worker = env
    with factory.begin() as db:
        event_id = mark(db).id
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    with factory.begin() as db:
        db.get(PoopEvent, event_id).bristol = 7
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_not_awaited(); bot.edit_message_caption.assert_awaited_once()
    with factory.begin() as db:
        db.delete(db.get(PoopEvent, event_id)); db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_awaited_once()
    assert 'пока нет отметок' in bot.edit_message_media.await_args.kwargs['media'].caption


def test_new_revision_while_sending_is_not_consumed(env):
    factory, bot, worker = env
    rid = queued(factory)
    async def sending(**kwargs):
        with factory.begin() as db:
            mark(db); db.get(WeeklyReport, rid).revision += 1
        return SimpleNamespace(message_id=10)
    bot.send_photo.side_effect = sending
    asyncio.run(worker.deliver(rid))
    assert worker.get(rid).delivered_revision == 1
    assert worker.get(rid).revision == 2
    assert worker.pending()[0] == [rid]
    asyncio.run(worker.deliver(rid))
    assert worker.get(rid).delivered_revision == 2
    bot.send_photo.assert_awaited_once(); bot.edit_message_media.assert_awaited_once()


def test_failed_caption_refresh_survives_new_worker(env):
    factory, bot, worker = env
    with factory.begin() as db:
        eid = mark(db).id
    rid = queued(factory)
    asyncio.run(worker.deliver(rid))
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
        db.get(WeeklyReport, rid).revision += 1
    bot.edit_message_caption.side_effect = OSError('network')
    asyncio.run(worker.deliver(rid))
    row = worker.get(rid)
    assert row.photo_message_id == 10 and row.delivered_revision == 1 and row.attempts == 1
    assert worker.pending()[0] == [] and worker.pending()[1] > 0
    bot.edit_message_caption.side_effect = None
    worker = WeeklyReportWorker(bot, factory, 'unused')
    asyncio.run(worker.deliver(rid))
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    assert worker.get(rid).delivered_revision == 2


def test_missing_published_card_is_not_reposted(env):
    factory, bot, worker = env
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    with factory.begin() as db:
        mark(db); db.get(WeeklyReport, rid).revision += 1
    bot.edit_message_media.side_effect = TelegramBadRequest(method=EditMessageMedia(chat_id=-1, message_id=10,
        media={'type':'photo','media':'x'}), message='Bad Request: message to edit not found')
    asyncio.run(worker.deliver(rid))
    assert worker.get(rid).disabled
    bot.send_photo.assert_awaited_once()


def test_rate_limit_and_retry_exhaustion_preserve_job(env):
    factory, bot, worker = env
    rid = queued(factory)
    exc = TelegramRetryAfter(method=SendPhoto(chat_id=-1, photo='x'), message='Too Many Requests', retry_after=800)
    bot.send_photo.side_effect = exc
    asyncio.run(worker.deliver(rid))
    assert worker.pending()[1] > 790
    with factory.begin() as db:
        db.get(WeeklyReport, rid).attempts = MAX_ATTEMPTS-1
    asyncio.run(worker.deliver(rid))
    assert worker.pending() == ([], None)
    assert not worker.get(rid).disabled and worker.get(rid).delivered_revision == 0


def test_caption_fits_telegram_and_includes_quality_trend_and_unknowns(env):
    from app.services.weekly_report_caption import build_caption
    factory, _, _ = env
    with factory.begin() as db:
        mark(db); mark(db, n=2, uid=2).bristol = None
        mark(db, day=START-timedelta(days=1))
        db.flush()
        card = project_card(db, -1, START, END, '<&>🚀'*80, label='Превью'*10)
        caption = build_caption(db, -1, card)
    assert len(caption.encode('utf-16-le'))//2 <= 1024
    assert 'не указано: 1' in caption and '+1 отметок (было 1)' in caption
    assert 'Консистенция' in caption and 'Самочувствие' in caption
    assert '/stats' in caption


def test_legacy_text_is_removed_after_caption_is_saved(env):
    factory, bot, worker = env
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    with factory.begin() as db:
        row = db.get(WeeklyReport, rid)
        row.text_messages = json.dumps([{'id': 11, 'hash':'old'}])
        row.caption_hash = None; row.revision += 1
    asyncio.run(worker.deliver(rid))
    bot.send_photo.assert_awaited_once(); bot.send_message.assert_not_awaited()
    bot.edit_message_caption.assert_awaited_once()
    assert bot.delete_message.await_args.kwargs['message_id'] == 11
    assert worker.get(rid).text_messages == '[]'
