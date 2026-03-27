from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, ChatMember, PoopEvent, Session as DaySession, SessionUserState, User, UserStreak
from app.services.q1_service import (
    render_q1,
    render_q1_private,
    restore_recent_streak_window,
    restore_streak_for_user,
    restore_streak_target_date,
    should_show_restore_streak_button,
    undo_recent_streak_window,
)


class RestoreStreakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.env_patch = patch.dict("os.environ", {"Q1_RESTORE_BUTTON_ENABLED": "true"}, clear=False)
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db.close()
        self.engine.dispose()

    def _add_chat(self, chat_id: int) -> None:
        self.db.add(Chat(chat_id=chat_id, timezone="Europe/Minsk", is_enabled=True))

    def _add_user(self, user_id: int) -> None:
        self.db.add(User(user_id=user_id, username=f"u{user_id}", first_name="U", last_name=str(user_id)))

    def _add_membership(self, chat_id: int, user_id: int) -> None:
        self.db.add(ChatMember(chat_id=chat_id, user_id=user_id))

    def _add_day(self, *, chat_id: int, user_id: int, session_date: date, with_event: bool) -> None:
        sess = DaySession(chat_id=chat_id, session_date=session_date, status="closed")
        self.db.add(sess)
        self.db.flush()
        self.db.add(
            SessionUserState(
                session_id=sess.session_id,
                user_id=user_id,
                poops_n=1 if with_event else 0,
            )
        )
        if with_event:
            self.db.add(
                PoopEvent(
                    session_id=sess.session_id,
                    user_id=user_id,
                    event_n=1,
                    origin_chat_id=chat_id,
                )
            )

    def test_restore_target_is_yesterday_only_when_gap_is_exactly_one_day(self) -> None:
        chat_id = -100
        user_id = 1
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_membership(chat_id, user_id)
        self._add_day(chat_id=chat_id, user_id=user_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.commit()

        assert restore_streak_target_date(self.db, chat_id, user_id, today) == date(2026, 3, 22)

    def test_restore_streak_backfills_yesterday_and_refreshes_cached_streak(self) -> None:
        chat_id = -100
        user_id = 1
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_membership(chat_id, user_id)
        self._add_day(chat_id=chat_id, user_id=user_id, session_date=date(2026, 3, 20), with_event=True)
        self._add_day(chat_id=chat_id, user_id=user_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.add(UserStreak(chat_id=chat_id, user_id=user_id, current_streak=0, last_poop_date=date(2026, 3, 21)))
        self.db.commit()

        changed, message = restore_streak_for_user(self.db, chat_id, user_id, today)
        self.db.commit()

        assert changed is True
        assert message == "Стрик за вчера восстановлен"

        restored_session = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=date(2026, 3, 22)).one()
        restored_state = self.db.get(SessionUserState, {"session_id": restored_session.session_id, "user_id": user_id})
        restored_event = self.db.query(PoopEvent).filter_by(session_id=restored_session.session_id, user_id=user_id, event_n=1).one()
        streak = self.db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})

        assert restored_state is not None
        assert restored_state.poops_n == 1
        assert restored_event.origin_chat_id == chat_id
        assert streak is not None
        assert streak.last_poop_date == date(2026, 3, 22)
        assert streak.current_streak == 3

    def test_restore_button_visible_for_private_chat_owner_when_gap_exists(self) -> None:
        chat_id = 7
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(chat_id)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.commit()

        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=today,
            viewer_user_id=chat_id,
            is_private_chat=True,
        ) is True

    def test_restore_button_not_visible_in_following_session_if_user_skipped_recovery(self) -> None:
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_membership(chat_id, user_id)
        self._add_day(chat_id=chat_id, user_id=user_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.commit()

        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=date(2026, 3, 23),
            viewer_user_id=None,
            is_private_chat=False,
        ) is True

        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=date(2026, 3, 24),
            viewer_user_id=None,
            is_private_chat=False,
        ) is False

    def test_restore_button_disappears_after_successful_recovery(self) -> None:
        chat_id = 7
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(chat_id)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.commit()

        changed, _message = restore_streak_for_user(self.db, chat_id, chat_id, today)
        self.db.commit()

        assert changed is True
        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=today,
            viewer_user_id=chat_id,
            is_private_chat=True,
        ) is False

    def test_restore_streak_cannot_be_applied_twice(self) -> None:
        chat_id = -100
        user_id = 1
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_membership(chat_id, user_id)
        self._add_day(chat_id=chat_id, user_id=user_id, session_date=date(2026, 3, 21), with_event=True)
        self.db.commit()

        first_changed, _ = restore_streak_for_user(self.db, chat_id, user_id, today)
        self.db.commit()
        second_changed, second_message = restore_streak_for_user(self.db, chat_id, user_id, today)
        self.db.commit()

        assert first_changed is True
        assert second_changed is False
        assert second_message == "Тебе нечего восстанавливать"

    def test_restore_streak_preserves_today_positive_answer_in_live_streak(self) -> None:
        chat_id = 7
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(chat_id)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=date(2026, 3, 21), with_event=True)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=today, with_event=True)
        self.db.commit()

        changed, _message = restore_streak_for_user(self.db, chat_id, chat_id, today)
        self.db.commit()

        today_sess = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=today).one()
        text = render_q1_private(self.db, chat_id=chat_id, session_id=today_sess.session_id, user_id=chat_id, session_date=today)

        assert changed is True
        assert "Стрик в этой личке: 3 дн." in text

    def test_q1_group_shows_zero_streak_for_new_member(self) -> None:
        chat_id = -100
        user_id = 1
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_membership(chat_id, user_id)
        sess = DaySession(chat_id=chat_id, session_date=today, status="active")
        self.db.add(sess)
        self.db.commit()

        text = render_q1(self.db, chat_id=chat_id, session_id=sess.session_id, session_date=today)

        self.assertIn("@u1 — — | стрик 0 дн.", text)

    def test_undo_restore_removes_backfilled_day_and_live_streak_falls_back(self) -> None:
        chat_id = 7
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(chat_id)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=date(2026, 3, 21), with_event=True)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=today, with_event=True)
        self.db.commit()

        changed, _message = restore_recent_streak_window(
            self.db,
            chat_id=chat_id,
            user_id=chat_id,
            current_session_date=today,
        )
        self.db.commit()
        self.assertTrue(changed)

        undone, undo_message = undo_recent_streak_window(
            self.db,
            chat_id=chat_id,
            user_id=chat_id,
            current_session_date=today,
        )
        self.db.commit()

        today_sess = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=today).one()
        restored_sess = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=date(2026, 3, 22)).one()
        restored_state = self.db.get(SessionUserState, {"session_id": restored_sess.session_id, "user_id": chat_id})
        text = render_q1_private(self.db, chat_id=chat_id, session_id=today_sess.session_id, user_id=chat_id, session_date=today)

        self.assertTrue(undone)
        self.assertEqual(undo_message, "Отменено 6 дн. восстановления")
        self.assertEqual(int(restored_state.poops_n), 0)
        self.assertEqual(
            self.db.query(PoopEvent).filter_by(session_id=restored_sess.session_id, user_id=chat_id).count(),
            0,
        )
        self.assertIn("Стрик в этой личке: 1 дн.", text)

    def test_recent_restore_fills_all_missing_days_in_last_week(self) -> None:
        chat_id = 7
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(chat_id)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=date(2026, 3, 20), with_event=True)
        self._add_day(chat_id=chat_id, user_id=chat_id, session_date=today, with_event=True)
        self.db.commit()

        changed, message = restore_recent_streak_window(
            self.db,
            chat_id=chat_id,
            user_id=chat_id,
            current_session_date=today,
        )
        self.db.commit()

        for missing_day in (
            date(2026, 3, 16),
            date(2026, 3, 17),
            date(2026, 3, 18),
            date(2026, 3, 19),
            date(2026, 3, 21),
            date(2026, 3, 22),
        ):
            sess = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=missing_day).one()
            state = self.db.get(SessionUserState, {"session_id": sess.session_id, "user_id": chat_id})
            self.assertEqual(int(state.poops_n), 1)

        today_sess = self.db.query(DaySession).filter_by(chat_id=chat_id, session_date=today).one()
        text = render_q1_private(self.db, chat_id=chat_id, session_id=today_sess.session_id, user_id=chat_id, session_date=today)

        self.assertTrue(changed)
        self.assertEqual(message, "Восстановлено 6 дн. за последние 7 дней")
        self.assertIn("Стрик в этой личке: 8 дн.", text)

    def test_group_restore_button_visible_when_any_member_is_eligible(self) -> None:
        chat_id = -100
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(1)
        self._add_user(2)
        self._add_membership(chat_id, 1)
        self._add_membership(chat_id, 2)
        self._add_day(chat_id=chat_id, user_id=1, session_date=date(2026, 3, 21), with_event=True)
        self._add_day(chat_id=chat_id, user_id=2, session_date=date(2026, 3, 22), with_event=True)
        self.db.commit()

        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=today,
            viewer_user_id=None,
            is_private_chat=False,
        ) is True

    def test_group_restore_button_hidden_when_no_member_is_eligible(self) -> None:
        chat_id = -100
        today = date(2026, 3, 23)
        self._add_chat(chat_id)
        self._add_user(1)
        self._add_user(2)
        self._add_membership(chat_id, 1)
        self._add_membership(chat_id, 2)
        self._add_day(chat_id=chat_id, user_id=1, session_date=date(2026, 3, 20), with_event=True)
        self._add_day(chat_id=chat_id, user_id=2, session_date=date(2026, 3, 22), with_event=True)
        self.db.commit()

        assert should_show_restore_streak_button(
            self.db,
            chat_id=chat_id,
            session_date=today,
            viewer_user_id=None,
            is_private_chat=False,
        ) is False


if __name__ == "__main__":
    unittest.main()
