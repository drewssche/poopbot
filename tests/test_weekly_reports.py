import asyncio
from datetime import date, timedelta
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import EditMessageMedia, GetChat, SendPhoto
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
    assert image.size == (840, 668)
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


def test_weekly_quality_edit_updates_visual_and_delete_clears_card(env):
    factory, bot, worker = env
    with factory.begin() as db:
        event_id = mark(db).id
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    with factory.begin() as db:
        db.get(PoopEvent, event_id).bristol = 7
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_awaited_once(); bot.edit_message_caption.assert_not_awaited()
    with factory.begin() as db:
        db.delete(db.get(PoopEvent, event_id)); db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    assert bot.edit_message_media.await_count == 2
    assert worker.get(rid).delivered_revision == worker.get(rid).revision


@pytest.mark.parametrize('period', ['month', 'year'])
def test_quality_edit_skips_render_after_restart_for_each_period(env, monkeypatch, period):
    from app.services import report_image_cache as cache
    from app.services.weekly_reports import enqueue_report
    factory, bot, worker = env
    with factory.begin() as db:
        eid = mark(db).id
        rid = enqueue_report(db, -1, START, period=period, chat_title='Тест').id
    asyncio.run(worker.deliver(rid))
    cache.clear_image_cache()
    def forbidden_render(*args):
        raise AssertionError('Quality-only edit must not render')
    monkeypatch.setattr(cache.renderer, 'render_card', forbidden_render)
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
        db.get(WeeklyReport, rid).revision += 1
    worker = WeeklyReportWorker(bot, factory, 'unused')
    asyncio.run(worker.deliver(rid))
    bot.edit_message_caption.assert_awaited_once()
    bot.edit_message_media.assert_not_awaited()
    assert worker.get(rid).delivered_revision == worker.get(rid).revision


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
        db.get(WeeklyReport, rid).chat_title = 'Переименованный чат'
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


@pytest.mark.parametrize('destination', [1, -1])
def test_forbidden_recipient_stops_job_and_only_alerts_for_groups(env, caplog, destination):
    factory, bot, worker = env
    worker.owner_id = 99
    with factory.begin() as db:
        rid = enqueue_weekly_report(db, -1, END, destination=destination,
                                    chat_title='Тест').id
    bot.send_photo.side_effect = TelegramForbiddenError(
        method=SendPhoto(chat_id=destination, photo='x'), message='PRIVATE-API-DETAIL')
    asyncio.run(worker.deliver(rid))
    row = worker.get(rid)
    assert row.disabled and row.attempts == 1 and row.delivered_revision == 0
    assert row.last_error == 'TelegramForbiddenError'
    assert worker.pending() == ([], None)
    asyncio.run(worker.deliver(rid))
    bot.send_photo.assert_awaited_once()
    if destination > 0:
        bot.send_message.assert_not_awaited()
    else:
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.kwargs['chat_id'] == 99
    assert 'TelegramForbiddenError' in caplog.text
    assert 'PRIVATE-API-DETAIL' not in caplog.text


def test_group_lookup_failure_still_alerts_for_private_preview(env):
    factory, bot, worker = env
    worker.owner_id = 99
    with factory.begin() as db:
        rid = enqueue_weekly_report(db, -1, END, destination=1).id
    bot.get_chat.side_effect = TelegramForbiddenError(
        method=GetChat(chat_id=-1), message='Forbidden')
    asyncio.run(worker.deliver(rid))
    assert worker.get(rid).disabled
    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_awaited_once()


def test_private_report_technical_failure_still_alerts_after_retries(env):
    factory, bot, worker = env
    worker.owner_id = 99
    with factory.begin() as db:
        row = enqueue_weekly_report(db, 1, END, chat_title='Личные отметки')
        row.attempts = MAX_ATTEMPTS - 1
        rid = row.id
    bot.send_photo.side_effect = OSError('network')
    asyncio.run(worker.deliver(rid))
    row = worker.get(rid)
    assert not row.disabled and row.attempts == MAX_ATTEMPTS
    assert worker.pending() == ([], None)
    bot.send_message.assert_awaited_once()


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


@pytest.mark.parametrize('period,anchor,start,end,previous_start,days', [
    ('month', date(2024, 2, 15), date(2024, 2, 1), date(2024, 2, 29), date(2024, 1, 1), 29),
    ('month', date(2026, 2, 28), date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), 28),
    ('month', date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 31), date(2025, 12, 1), 31),
    ('year', date(2024, 10, 1), date(2024, 1, 1), date(2024, 12, 31), date(2023, 1, 1), 366),
    ('year', date(2026, 10, 20), date(2026, 1, 1), date(2026, 12, 31), date(2025, 1, 1), 365),
])
def test_calendar_periods_boundaries_totals_comparison_and_single_delivery(env, period, anchor, start, end, previous_start, days):
    from app.services.weekly_reports import enqueue_report
    factory, bot, worker = env
    with factory.begin() as db:
        mark(db, day=start); mark(db, day=end)
        mark(db, day=start-timedelta(days=1))
        mark(db, day=end+timedelta(days=1))
        mark(db, day=start, uid=2, origin=-2)  # A copied mark must not enter this report.
        row = enqueue_report(db, -1, anchor, period=period, scheduled=True, chat_title='Чат')
        rid = row.id
        assert (row.period_start, row.period_end, row.previous_period_start) == (start, end, previous_start)
        card = project_card(db, -1, start, end, 'Чат', period=period)
        assert len(card.days) == days and card.total == 2 and card.active_days == 2
        if period == 'year':
            assert len(card.months) == 12 and card.months[0] == card.months[-1] == 1
            assert sum(card.months) == card.total
    asyncio.run(worker.deliver(rid))
    bot.send_photo.assert_awaited_once(); bot.send_message.assert_not_awaited()
    caption = bot.send_photo.await_args.kwargs['caption']
    assert f'из {days} дней' in caption
    assert ('К прошлому месяцу' if period == 'month' else 'К прошлому году') in caption
    assert '+1 отметок (было 1)' in caption
    assert len(caption.encode('utf-16-le'))//2 <= 1024
    from app.db.models import CommandMessage
    with factory() as db:
        assert db.scalar(select(CommandMessage.message_id)) == 10


@pytest.mark.parametrize('period', ['month', 'year'])
def test_month_year_edit_delete_restore_in_place_with_fixed_period(env, period):
    from app.services.weekly_reports import enqueue_report
    factory, bot, worker = env
    with factory.begin() as db:
        eid = mark(db).id
        rid = enqueue_report(db, -1, START, period=period, scheduled=True, chat_title='Чат').id
    asyncio.run(worker.deliver(rid))
    before = worker.get(rid)
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 7
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    bot.edit_message_caption.assert_awaited_once(); bot.edit_message_media.assert_not_awaited()
    with factory.begin() as db:
        db.delete(db.get(PoopEvent, eid)); db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    assert 'пока нет отметок' in bot.edit_message_media.await_args.kwargs['media'].caption
    with factory.begin() as db:
        mark(db).source = 'restore'; db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    assert bot.edit_message_media.await_count == 2
    bot.send_photo.assert_awaited_once(); bot.send_message.assert_not_awaited()
    after = worker.get(rid)
    assert after.photo_message_id == before.photo_message_id
    assert (after.period_start, after.period_end) == (before.period_start, before.period_end)


@pytest.mark.parametrize('period', ['month', 'year'])
def test_personal_month_year_excludes_group_and_other_users(env, period):
    from app.services.stats_common import period_to_range
    factory, _, _ = env
    with factory.begin() as db:
        mark(db, chat=1); mark(db, chat=1, uid=2); mark(db, chat=-1)
        r = period_to_range(START, period)
        card = project_card(db, 1, r.start, r.end, 'Личные', period=period)
        assert card.total == 1 and len(card.participants) == 1


@pytest.mark.parametrize('period,anchor', [('month', date(2026, 8, 1)), ('year', date(2024, 1, 1))])
def test_long_period_rendering_empty_and_dense_counts(env, period, anchor):
    from dataclasses import replace
    from app.services.stats_common import period_to_range
    factory, _, _ = env
    r = period_to_range(anchor, period)
    with factory() as db:
        empty = project_card(db, -1, r.start, r.end, 'Чат', period=period)
    dense = replace(empty, days=(1234,)*empty.period_days,
                    participants=tuple(Participant('Длинное имя '*20, 1234) for _ in range(12)))
    for card in (empty, dense):
        data = render_card(card)
        image = Image.open(BytesIO(data))
        assert image.size == (840, 1138 if period == 'month' else 940)
        assert len(data) < 1024*1024


def test_period_keys_do_not_collide_on_same_year_month_week_boundary(env):
    from app.services.weekly_reports import enqueue_report
    factory, _, _ = env
    with factory.begin() as db:
        rows = [enqueue_report(db, -1, date(2023, 12, 31), period=p, scheduled=True, chat_title='Чат')
                for p in ('week', 'month', 'year')]
        assert len({r.id for r in rows}) == 3
        assert len({r.delivery_key for r in rows}) == 3
        for row in rows:
            assert enqueue_report(db, -1, date(2023, 12, 31), period=row.report_period, scheduled=True).id == row.id


def test_compact_card_omits_names_and_title_and_marks_one():
    from dataclasses import replace
    card = WeeklyCard(START, END, 'Чат', (0,1,2,0,0,0,0), (Participant('Аня', 3),))
    picture = render_card(card)
    # Names/title are in the message; changing them cannot change the image.
    assert render_card(replace(card, chat_title='Другой чат',
        participants=(Participant('Другое имя', 3),))) == picture
    image = Image.open(BytesIO(picture))
    for index, has_badge in [(0, False), (1, True), (2, True)]:
        cx = 48 + (744 / 7) * (index + .5)
        # Top inside edge of the badge, away from its digit.
        assert (image.getpixel((round(cx+25), 314)) == (255,219,0)) == has_badge


def test_visual_weekly_details_have_scoped_answers_previous_period_and_rank(env):
    factory, _, _ = env
    with factory.begin() as db:
        for n, br in enumerate((1, 3, 6, 7, None), 1):
            event = mark(db, n=n)
            event.bristol = br
            event.feeling = ('great', 'ok', 'bad', None, 'ok')[n-1]
        mark(db, origin=-2, n=6)  # Copied mark excluded.
        for n in range(1, 7):
            mark(db, chat=-2, n=n)
        mark(db, day=START-timedelta(days=1))
        mark(db, day=START-timedelta(days=1), n=2)
        db.flush()
        card = project_card(db, -1, START, END, 'Чат', layout='weekly')
        assert card.total == 5
        assert card.details.bristol == (1,1,1,1,1)
        assert card.details.feeling == (1,2,1,1)
        assert card.details.previous_total == 2
        assert (card.details.rank, card.details.rank_total, card.details.previous_rank) == (2,2,1)
        picture = render_card(card)
        assert Image.open(BytesIO(picture)).height > 992  # Space for all recorded answer groups.
        from app.services.weekly_report_caption import build_caption
        caption = build_caption(db, -1, card)
        assert 'Консистенция' not in caption and 'К прошлой' not in caption
        assert '/stats' in caption and 'Чат' in caption


def test_private_weekly_excludes_other_users_and_group_rank(env):
    factory, _, _ = env
    with factory.begin() as db:
        mark(db, chat=1)
        mark(db, chat=1, uid=2).bristol = 7
        mark(db, chat=-1)
        card = project_card(db, 1, START, END, 'Личные', layout='weekly')
        assert card.total == 1 and card.details.bristol == (0,1,0,0,0)
        assert card.details.rank is None and card.details.rank_total == 0
        assert len(card.participants) == 1 and card.participants[0].name == 'Аня'
        assert render_card(card)


def test_weekly_same_bucket_edit_does_not_render_after_restart(env, monkeypatch):
    from app.services import report_image_cache as cache
    factory, bot, worker = env
    with factory.begin() as db:
        eid = mark(db).id
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    cache.clear_image_cache()
    def forbidden(*args):
        raise AssertionError('Same visual answer bucket must not render')
    monkeypatch.setattr(cache.renderer, 'render_card', forbidden)
    with factory.begin() as db:
        db.get(PoopEvent, eid).bristol = 4  # Still the same visible category as 3.
        db.get(WeeklyReport, rid).revision += 1
    worker = WeeklyReportWorker(bot, factory, 'unused')
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_not_awaited()
    bot.edit_message_caption.assert_not_awaited()
    assert worker.get(rid).delivered_revision == worker.get(rid).revision


def test_previous_week_change_refreshes_visual_comparison(env):
    factory, bot, worker = env
    rid = queued(factory); asyncio.run(worker.deliver(rid))
    before = worker.get(rid).visual_hash
    with factory.begin() as db:
        mark(db, day=START-timedelta(days=1))
        db.get(WeeklyReport, rid).revision += 1
    asyncio.run(worker.deliver(rid))
    bot.edit_message_media.assert_awaited_once()
    assert worker.get(rid).visual_hash != before


@pytest.mark.parametrize('private', [False, True])
def test_visual_weekly_empty_and_cross_year_periods(private):
    from app.services.weekly_report_card import ReportCard, WeeklyDetails
    start = date(2026,12,28)
    card = ReportCard(start, start+timedelta(days=6), 'Чат', (0,)*7, (), private,
        layout='weekly', details=WeeklyDetails(0,0,(0,)*5,(0,)*4))
    image = Image.open(BytesIO(render_card(card)))
    assert image.size == (840,992)
