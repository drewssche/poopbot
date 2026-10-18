import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from sqlalchemy import create_engine, select, delete
from sqlalchemy.orm import sessionmaker

from app.bot.handlers import callbacks_restore as handler
from app.bot.handlers import callbacks_streak_admin as admin
from app.db.base import Base
from app.db.models import Chat, Session as DaySession, SessionMessage, SessionUserState, PoopEvent, User, UserStreak, RateLimit
from app.db.session import db_session
from app.services import scheduler_service as scheduler
from app.services.app_setting_service import RESTORE_CLAIM_ENABLED_KEY, set_bool_setting
from app.services.restore_period_service import (
    RestorePeriod, choose_restore_period, ensure_restore_period, get_restore_period,
    mark_forgotten_day, restore_period, undo_period,
)

TODAY = date(2026, 10, 18)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'restore.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([Chat(chat_id=-100), Chat(chat_id=-200), Chat(chat_id=1), User(user_id=1, first_name="Test"),
                    User(user_id=2, first_name="Other")])
    yield factory
    engine.dispose()


def add_day(db, day, *, chat_id=-100, user_id=1, count=0, status="closed", message_id=10):
    sess = DaySession(chat_id=chat_id, session_date=day, status=status)
    db.add(sess)
    db.flush()
    db.add(SessionMessage(session_id=sess.session_id, kind="Q1", message_id=message_id))
    if count:
        db.add(SessionUserState(session_id=sess.session_id, user_id=user_id, poops_n=count))
        for n in range(1, count + 1):
            db.add(PoopEvent(session_id=sess.session_id, user_id=user_id, event_n=n, origin_chat_id=chat_id))
    db.flush()
    return sess


def marks(db, chat_id=-100, user_id=1):
    return db.execute(select(DaySession.session_date, SessionUserState.poops_n).join(SessionUserState).where(
        DaySession.chat_id == chat_id, SessionUserState.user_id == user_id,
        SessionUserState.poops_n > 0,
    ).order_by(DaySession.session_date)).all()


def test_period_is_persisted_and_never_rolls_forward(factory):
    with factory.begin() as db:
        period = ensure_restore_period(db, today=TODAY)
        assert period.start == period.end == TODAY - timedelta(days=1)
    with factory.begin() as db:
        assert ensure_restore_period(db, today=TODAY + timedelta(days=5)) == period
        choose_restore_period(db, today=TODAY, days=3)
    with factory() as db:
        assert get_restore_period(db) == RestorePeriod(date(2026, 10, 15), date(2026, 10, 17))


@pytest.mark.parametrize("days", [0, -1, 31])
def test_invalid_period_rejected(factory, days):
    with factory() as db, pytest.raises(ValueError):
        choose_restore_period(db, today=TODAY, days=days)


def test_restore_is_local_idempotent_and_undo_preserves_real_marks(factory):
    with factory.begin() as db:
        period = choose_restore_period(db, today=TODAY, days=3)
        add_day(db, period.start, count=2)
        add_day(db, TODAY, count=1, status="active", message_id=11)
        add_day(db, period.end, chat_id=-200, count=1)
        changed, _ = restore_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)
        assert changed
        assert not restore_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)[0]
        assert marks(db) == [(period.start, 2), (date(2026, 10, 16), 1), (period.end, 1), (TODAY, 1)]
        assert marks(db, -100, 2) == []
        assert marks(db, -200) == [(period.end, 1)]
        assert db.get(UserStreak, (-100, 1)).current_streak == 3
        assert undo_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)[0]
        assert marks(db) == [(period.start, 2), (TODAY, 1)]


def test_restore_uses_current_day_to_recalculate_old_period(factory):
    with factory.begin() as db:
        period = choose_restore_period(db, today=TODAY - timedelta(days=7), days=1)
        restore_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)
        assert db.get(UserStreak, (-100, 1)).current_streak == 0
        assert marks(db) == [(period.end, 1)]


def test_period_cannot_restore_current_or_future_day(factory):
    with factory.begin() as db:
        assert not restore_period(db, chat_id=-100, user_id=1, period=RestorePeriod(TODAY, TODAY), today=TODAY)[0]
        assert marks(db) == []


@pytest.mark.parametrize("offset,status,allowed", [(0, "closed", True), (1, "closed", True), (2, "closed", False), (-1, "closed", False), (1, "active", False)])
def test_forgotten_day_expiry(factory, offset, status, allowed):
    with factory.begin() as db:
        sess = add_day(db, TODAY - timedelta(days=offset), status=status)
        changed, _ = mark_forgotten_day(db, sess=sess, user_id=1, today=TODAY)
        assert changed is allowed
        assert not mark_forgotten_day(db, sess=sess, user_id=1, today=TODAY)[0]
        assert len(marks(db)) == int(allowed)


@pytest.fixture
def callbacks(factory, monkeypatch):
    now = datetime(2026, 10, 18, 23, 59, 59, tzinfo=timezone(timedelta(hours=3)))
    monkeypatch.setattr(handler, "init_db", lambda _: None)
    monkeypatch.setattr(handler, "_session_factory", factory)
    monkeypatch.setattr(handler, "load_settings", lambda: SimpleNamespace(database_url="unused"))
    monkeypatch.setattr(handler, "now_in_tz", lambda _: now)
    monkeypatch.setattr(scheduler, "now_in_tz", lambda _: now)
    cb = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup"), message_id=10),
        from_user=SimpleNamespace(id=1, username="test", first_name="Test", last_name=None),
        data="q1:forgot", answer=AsyncMock(),
        bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )
    return cb


def test_forgotten_callback_works_with_toggle_off_and_expired_answer(factory, callbacks):
    cb = callbacks
    with factory.begin() as db:
        add_day(db, TODAY - timedelta(days=2), count=1, message_id=9)
        sess = add_day(db, TODAY - timedelta(days=1))
        sid = sess.session_id
        add_day(db, TODAY, status="active", message_id=11)
        set_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, False)

    async def expired(*args, **kwargs):
        with factory() as db:
            assert db.get(SessionUserState, (sid, 1)).poops_n == 1
        raise TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id="x"), message="query is too old")
    cb.answer.side_effect = expired
    asyncio.run(handler.forgotten_day_claim(cb))
    with factory() as db:
        assert db.get(UserStreak, (-100, 1)).current_streak == 2
        assert marks(db) == [(TODAY - timedelta(days=2), 1), (TODAY - timedelta(days=1), 1)]
        assert marks(db, -200) == []
    assert cb.bot.edit_message_text.await_count == 2
    closed = cb.bot.edit_message_text.await_args_list[0].kwargs
    assert closed["message_id"] == 10
    assert closed["reply_markup"].inline_keyboard[0][0].callback_data == "q1:forgot"
    asyncio.run(handler.forgotten_day_claim(cb))
    with factory() as db:
        assert db.get(SessionUserState, (sid, 1)).poops_n == 1


def test_forgotten_callback_rejects_at_next_midnight(factory, callbacks, monkeypatch):
    with factory.begin() as db:
        add_day(db, TODAY - timedelta(days=1))
    monkeypatch.setattr(handler, "now_in_tz", lambda _: datetime(2026, 10, 19, tzinfo=timezone(timedelta(hours=3))))
    asyncio.run(handler.forgotten_day_claim(callbacks))
    with factory() as db:
        assert marks(db) == []
    assert "Срок истёк" in callbacks.answer.await_args.args[0]
    callbacks.bot.edit_message_text.assert_not_awaited()


def test_forgotten_callback_cannot_use_other_message_or_chat(factory, callbacks):
    with factory.begin() as db:
        add_day(db, TODAY - timedelta(days=1), chat_id=-200)
    asyncio.run(handler.forgotten_day_claim(callbacks))
    with factory() as db:
        assert marks(db, -200) == []
        assert marks(db) == []


def test_same_service_button_uses_updated_global_period(factory, callbacks):
    cb = callbacks
    cb.data = "restore:claim"
    with factory.begin() as db:
        set_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, True)
        choose_restore_period(db, today=TODAY, days=1)
    asyncio.run(handler.restore_streak_claim(cb))
    with factory.begin() as db:
        assert marks(db) == [(date(2026, 10, 17), 1)]
        choose_restore_period(db, today=TODAY, days=3)
        db.execute(delete(RateLimit))
    asyncio.run(handler.restore_streak_claim(cb))
    with factory() as db:
        assert marks(db) == [(date(2026, 10, 15), 1), (date(2026, 10, 16), 1), (date(2026, 10, 17), 1)]
    assert "15.10.2026–17.10.2026" in cb.answer.await_args.args[0]


def test_service_button_respects_global_switch(factory, callbacks):
    with factory.begin() as db:
        choose_restore_period(db, today=TODAY, days=1)
        set_bool_setting(db, RESTORE_CLAIM_ENABLED_KEY, False)
    asyncio.run(handler.restore_streak_claim(callbacks))
    with factory() as db:
        assert marks(db) == []
    assert "отключено" in callbacks.answer.await_args.args[0]


@pytest.mark.parametrize("owner", [True, False])
def test_admin_period_selection_checks_owner_and_persists(factory, callbacks, monkeypatch, owner):
    cb = callbacks
    cb.message.chat.id = 1
    cb.message.chat.type = "private"
    cb.data = "streakadmin:period:3"
    monkeypatch.setattr(admin, "load_settings", lambda: SimpleNamespace(bot_owner_id=1 if owner else 999, database_url="unused"))
    monkeypatch.setattr(admin, "init_db", lambda _: None)
    monkeypatch.setattr(admin, "_session_factory", factory)
    monkeypatch.setattr(admin, "now_in_tz", lambda _: datetime(2026, 10, 18, 0, 1, tzinfo=timezone(timedelta(hours=3))))
    asyncio.run(admin.streak_admin_callbacks(cb))
    with factory() as db:
        period = get_restore_period(db)
        assert period == (RestorePeriod(date(2026, 10, 15), date(2026, 10, 17)) if owner else None)
    if owner:
        assert "15.10.2026–17.10.2026" in cb.bot.edit_message_text.await_args.kwargs["text"]



def test_undo_does_not_remove_forgotten_or_supplemented_marks(factory):
    with factory.begin() as db:
        period = choose_restore_period(db, today=TODAY, days=3)
        forgotten = add_day(db, period.end)
        mark_forgotten_day(db, sess=forgotten, user_id=1, today=TODAY)
        restore_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)
        edited = db.scalar(select(DaySession).where(DaySession.chat_id == -100, DaySession.session_date == period.start))
        event = db.scalar(select(PoopEvent).where(PoopEvent.session_id == edited.session_id))
        event.bristol = 4
        db.flush()
        undo_period(db, chat_id=-100, user_id=1, period=period, today=TODAY)
        assert marks(db) == [(period.start, 1), (period.end, 1)]


def test_closed_view_has_button_and_expired_view_does_not(factory, callbacks, monkeypatch):
    with factory.begin() as db:
        sess = add_day(db, TODAY - timedelta(days=1))
        asyncio.run(scheduler._lock_q1(callbacks.bot, db, -100, sess.session_id))
        payload = callbacks.bot.edit_message_text.await_args.kwargs
        assert "17.10" in payload["text"]
        assert payload["reply_markup"].inline_keyboard[0][0].text == "💩 Забыл отметиться"
        monkeypatch.setattr(scheduler, "now_in_tz", lambda _: datetime(2026, 10, 19))
        asyncio.run(scheduler._lock_q1(callbacks.bot, db, -100, sess.session_id))
        assert callbacks.bot.edit_message_text.await_args.kwargs["reply_markup"] is None


def test_startup_adds_button_to_existing_yesterday_message(factory, callbacks, monkeypatch):
    from app.services.time_service import SessionWindow
    monkeypatch.setattr(scheduler, "get_session_window", lambda _: SessionWindow(TODAY, False, True))
    monkeypatch.setattr(scheduler, "now_in_tz", lambda _: datetime(2026, 10, 18, 12))
    with factory.begin() as db:
        add_day(db, TODAY - timedelta(days=1))
        add_day(db, TODAY, status="active", message_id=11)
    assert not asyncio.run(scheduler._recover_chat_q1_on_startup(callbacks.bot, factory, -100))
    callbacks.bot.edit_message_text.assert_awaited_once()
    assert callbacks.bot.edit_message_text.await_args.kwargs["message_id"] == 10
    assert callbacks.bot.edit_message_text.await_args.kwargs["reply_markup"] is not None


def test_admin_switch_does_not_change_saved_period(factory, callbacks, monkeypatch):
    with factory.begin() as db:
        period = choose_restore_period(db, today=TODAY - timedelta(days=3), days=7)
    cb = callbacks
    cb.message.chat.id, cb.message.chat.type, cb.data = 1, "private", "streakadmin:toggle"
    monkeypatch.setattr(admin, "load_settings", lambda: SimpleNamespace(bot_owner_id=1, database_url="unused"))
    monkeypatch.setattr(admin, "init_db", lambda _: None)
    monkeypatch.setattr(admin, "_session_factory", factory)
    monkeypatch.setattr(admin, "now_in_tz", lambda _: datetime(2026, 10, 18))
    asyncio.run(admin.streak_admin_callbacks(cb))
    with factory() as db:
        assert get_restore_period(db) == period
