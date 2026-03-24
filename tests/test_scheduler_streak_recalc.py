"""Тесты для оптимизации scheduler_service._recalculate_streaks_from_history

Проверяет, что инкрементальная пересчитка стриков работает корректно.
"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, ChatMember, PoopEvent, Session as DaySession, User, UserStreak
from app.services.scheduler_service import _recalculate_streaks_from_history


class RecalculateStreaksIncrementalTests(unittest.TestCase):
    """Тесты инкрементальной пересчитки стриков."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.today = date(2026, 3, 24)
        self.yesterday = self.today - timedelta(days=1)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _add_chat(self, chat_id: int) -> None:
        self.db.add(Chat(chat_id=chat_id, timezone="Europe/Minsk", is_enabled=True))

    def _add_user(self, user_id: int, username: str = "u") -> None:
        self.db.add(User(user_id=user_id, username=username, first_name="U", last_name=str(user_id)))

    def _add_member(self, chat_id: int, user_id: int) -> None:
        self.db.add(ChatMember(chat_id=chat_id, user_id=user_id))

    def _add_session_and_event(self, chat_id: int, user_id: int, d: date) -> None:
        sess = DaySession(chat_id=chat_id, session_date=d, status="active")
        self.db.add(sess)
        self.db.flush()
        self.db.add(
            PoopEvent(
                session_id=sess.session_id,
                user_id=user_id,
                event_n=1,
                origin_chat_id=chat_id,
            )
        )

    def _add_streak(self, chat_id: int, user_id: int, current_streak: int, last_poop_date: date | None) -> None:
        self.db.add(
            UserStreak(
                chat_id=chat_id,
                user_id=user_id,
                current_streak=current_streak,
                last_poop_date=last_poop_date,
            )
        )

    def test_continues_streak_when_user_marked_yesterday(self) -> None:
        """Стрик продолжается, если пользователь отметился вчера."""
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_member(chat_id, user_id)

        # Был стрик 5 дней, последняя отметка - вчера
        self._add_streak(chat_id, user_id, current_streak=5, last_poop_date=self.yesterday)

        # Пользователь отметился вчера
        self._add_session_and_event(chat_id, user_id, self.yesterday)

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streak = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user_id).one()
        self.assertEqual(streak.current_streak, 6)  # 5 + 1
        self.assertEqual(streak.last_poop_date, self.yesterday)

    def test_resets_streak_when_user_did_not_mark_yesterday(self) -> None:
        """Стрик сбрасывается, если пользователь не отметился вчера."""
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_member(chat_id, user_id)

        # Был стрик 5 дней, последняя отметка - позавчера
        two_days_ago = self.yesterday - timedelta(days=1)
        self._add_streak(chat_id, user_id, current_streak=5, last_poop_date=two_days_ago)

        # Пользователь НЕ отметился вчера

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streak = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user_id).one()
        self.assertEqual(streak.current_streak, 0)
        # last_poop_date не сбрасывается — это история
        self.assertEqual(streak.last_poop_date, two_days_ago)

    def test_starts_new_streak_after_gap_when_user_marked_yesterday(self) -> None:
        """Новый стрик начинается после разрыва, если пользователь отметился вчера."""
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_member(chat_id, user_id)

        # Был стрик, но последняя отметка - 10 дней назад (разрыв)
        old_date = self.today - timedelta(days=10)
        self._add_streak(chat_id, user_id, current_streak=3, last_poop_date=old_date)

        # Пользователь отметился вчера (после разрыва)
        self._add_session_and_event(chat_id, user_id, self.yesterday)

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streak = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user_id).one()
        self.assertEqual(streak.current_streak, 1)  # Новый стрик
        self.assertEqual(streak.last_poop_date, self.yesterday)

    def test_handles_multiple_users_independently(self) -> None:
        """Стрики разных пользователей считаются независимо."""
        chat_id = -100
        user1, user2 = 1, 2
        self._add_chat(chat_id)
        self._add_user(user1, "u1")
        self._add_user(user2, "u2")
        self._add_member(chat_id, user1)
        self._add_member(chat_id, user2)

        # User1: отметился вчера, стрик продолжается
        self._add_streak(chat_id, user1, current_streak=3, last_poop_date=self.yesterday)
        self._add_session_and_event(chat_id, user1, self.yesterday)

        # User2: не отметился вчера, стрик сбрасывается
        self._add_streak(chat_id, user2, current_streak=5, last_poop_date=self.yesterday - timedelta(days=2))

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streak1 = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user1).one()
        streak2 = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user2).one()

        self.assertEqual(streak1.current_streak, 4)  # 3 + 1
        self.assertEqual(streak2.current_streak, 0)

    def test_creates_streak_row_if_missing(self) -> None:
        """Создаёт UserStreak, если строки нет."""
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id)
        self._add_member(chat_id, user_id)

        # Пользователь отметился вчера, но UserStreak нет
        self._add_session_and_event(chat_id, user_id, self.yesterday)

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streak = self.db.query(UserStreak).filter_by(chat_id=chat_id, user_id=user_id).one()
        self.assertEqual(streak.current_streak, 1)
        self.assertEqual(streak.last_poop_date, self.yesterday)

    def test_no_members_no_crash(self) -> None:
        """Нет членов чата — функция не падает."""
        chat_id = -100
        self._add_chat(chat_id)

        _recalculate_streaks_from_history(self.db, chat_id, self.today)

        streaks = self.db.query(UserStreak).filter_by(chat_id=chat_id).all()
        self.assertEqual(len(streaks), 0)


if __name__ == "__main__":
    unittest.main()
