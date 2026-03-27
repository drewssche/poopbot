from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
import sys
import types
import re
from datetime import timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, PoopEvent, Session as DaySession, SessionUserState, User

# Test-only fallback when local env does not have pytz installed.
if "pytz" not in sys.modules:
    pytz_stub = types.ModuleType("pytz")
    pytz_stub.timezone = lambda _name: timezone.utc
    sys.modules["pytz"] = pytz_stub

from app.services.stats_service import (
    _compute_user_chat_streak_live,
    build_stats_text_chat,
    build_stats_text_my,
    collect_among_chats_snapshot,
)


class StatsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _add_chat(self, chat_id: int, tz: str = "Europe/Minsk") -> None:
        self.db.add(
            Chat(
                chat_id=chat_id,
                timezone=tz,
                is_enabled=True,
                show_in_global=True,
            )
        )

    def _add_user(self, user_id: int, username: str | None = None) -> None:
        self.db.add(User(user_id=user_id, username=username, first_name="U", last_name=str(user_id)))

    def _add_session_state(
        self,
        *,
        chat_id: int,
        d: date,
        user_id: int,
        poops_n: int,
        with_events: bool,
        origin_chat_id: int | None = None,
    ) -> None:
        sess = self.db.query(DaySession).filter(DaySession.chat_id == chat_id, DaySession.session_date == d).one_or_none()
        if sess is None:
            sess = DaySession(chat_id=chat_id, session_date=d, status="active")
            self.db.add(sess)
            self.db.flush()

        st = self.db.get(SessionUserState, {"session_id": sess.session_id, "user_id": user_id})
        if st is None:
            st = SessionUserState(session_id=sess.session_id, user_id=user_id, poops_n=poops_n)
            self.db.add(st)
        else:
            st.poops_n = poops_n
        if with_events and poops_n > 0:
            origin = origin_chat_id if origin_chat_id is not None else chat_id
            for n in range(1, int(poops_n) + 1):
                self.db.add(
                    PoopEvent(
                        session_id=sess.session_id,
                        user_id=user_id,
                        event_n=n,
                        origin_chat_id=origin,
                    )
                )

    def _extract_slot_counts(self, text: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for slot in ("Ночь", "Утро", "День", "Вечер"):
            m = re.search(rf"{slot}.*?:\s+(\d+) раз", text)
            if m:
                out[slot] = int(m.group(1))
        return out

    def test_chat_streak_uses_origin_events_for_chat_context(self) -> None:
        chat_id = -100
        user_id = 1
        self._add_chat(chat_id)
        self._add_user(user_id, username="u1")

        start = date(2026, 2, 10)
        for i in range(7):
            d = start + timedelta(days=i)
            # First 3 days: state only (simulated sync). Last 4: with events.
            self._add_session_state(
                chat_id=chat_id,
                d=d,
                user_id=user_id,
                poops_n=1,
                with_events=i >= 3,
            )

        self.db.commit()
        streak = _compute_user_chat_streak_live(self.db, chat_id, user_id, date(2026, 2, 16))
        self.assertEqual(streak, 4)

    def test_private_block_streak_consistent_with_private_origin_dataset(self) -> None:
        chat_id = 200
        user_id = 7
        self._add_chat(chat_id)
        self._add_user(user_id, username="u7")

        start = date(2026, 2, 10)
        for i in range(7):
            d = start + timedelta(days=i)
            # State exists every day; local-origin events only in last 4 days.
            self._add_session_state(
                chat_id=chat_id,
                d=d,
                user_id=user_id,
                poops_n=1,
                with_events=i >= 3,
                origin_chat_id=chat_id,
            )

        self.db.commit()
        text = build_stats_text_chat(self.db, chat_id, date(2026, 2, 16), "all", user_id=user_id)
        self.assertIn("- Дней с 💩: 4/7", text)
        self.assertIn("- Текущий стрик: 4 дн.", text)
        self.assertIn("- Лучший стрик: 4 дн.", text)

    def test_among_chats_record_day_returns_all_winners_on_tie(self) -> None:
        c1, c2 = -1, -2
        self._add_chat(c1)
        self._add_chat(c2)
        self._add_user(1)
        self._add_user(2)

        d = date(2026, 2, 16)
        # Chat 1 total for day = 5
        self._add_session_state(chat_id=c1, d=d, user_id=1, poops_n=3, with_events=False)
        self._add_session_state(chat_id=c1, d=d, user_id=2, poops_n=2, with_events=False)
        # Chat 2 total for day = 5 (tie)
        self._add_session_state(chat_id=c2, d=d, user_id=1, poops_n=4, with_events=False)
        self._add_session_state(chat_id=c2, d=d, user_id=2, poops_n=1, with_events=False)

        self.db.commit()
        snap = collect_among_chats_snapshot(self.db, d)
        winners = {(cid, poops) for cid, _day, poops in snap["record_days"]}
        self.assertEqual(winners, {(c1, 5), (c2, 5)})

    def test_my_stats_slot_patterns_follow_canonical_day_total(self) -> None:
        user_id = 1
        c1, c2 = -1, -2
        d = date(2026, 2, 16)
        self._add_chat(c1)
        self._add_chat(c2)
        self._add_user(user_id, username="u1")

        self._add_session_state(chat_id=c1, d=d, user_id=user_id, poops_n=2, with_events=False)
        self._add_session_state(chat_id=c2, d=d, user_id=user_id, poops_n=3, with_events=False)
        self.db.commit()

        s1 = self.db.query(DaySession).filter(DaySession.chat_id == c1, DaySession.session_date == d).one()
        s2 = self.db.query(DaySession).filter(DaySession.chat_id == c2, DaySession.session_date == d).one()
        self.db.add_all(
            [
                PoopEvent(session_id=s1.session_id, user_id=user_id, event_n=1, origin_chat_id=c1, created_at=datetime(2026, 2, 16, 3, 0)),
                PoopEvent(session_id=s1.session_id, user_id=user_id, event_n=2, origin_chat_id=c1, created_at=datetime(2026, 2, 16, 4, 0)),
                PoopEvent(session_id=s2.session_id, user_id=user_id, event_n=1, origin_chat_id=c2, created_at=datetime(2026, 2, 16, 13, 0)),
                PoopEvent(session_id=s2.session_id, user_id=user_id, event_n=2, origin_chat_id=c2, created_at=datetime(2026, 2, 16, 14, 0)),
                PoopEvent(session_id=s2.session_id, user_id=user_id, event_n=3, origin_chat_id=c2, created_at=datetime(2026, 2, 16, 15, 0)),
            ]
        )
        self.db.commit()

        text = build_stats_text_my(self.db, c1, user_id, d, "all")
        slot_counts = self._extract_slot_counts(text)

        self.assertIn("- Всего: 💩(3)", text)
        self.assertEqual(sum(slot_counts.values()), 3)
        self.assertNotIn("Пока нет данных для анализа паттернов.", text)

    def test_private_stats_slot_patterns_use_real_event_timestamps(self) -> None:
        chat_id = 200
        user_id = 7
        self._add_chat(chat_id)
        self._add_user(user_id, username="u7")

        d = date(2026, 2, 16)
        self._add_session_state(
            chat_id=chat_id,
            d=d,
            user_id=user_id,
            poops_n=2,
            with_events=False,
            origin_chat_id=chat_id,
        )
        self.db.commit()

        sess = self.db.query(DaySession).filter(DaySession.chat_id == chat_id, DaySession.session_date == d).one()
        self.db.add_all(
            [
                PoopEvent(session_id=sess.session_id, user_id=user_id, event_n=1, origin_chat_id=chat_id, created_at=datetime(2026, 2, 16, 9, 0)),
                PoopEvent(session_id=sess.session_id, user_id=user_id, event_n=2, origin_chat_id=chat_id, created_at=datetime(2026, 2, 16, 19, 0)),
            ]
        )
        self.db.commit()

        text = build_stats_text_chat(self.db, chat_id, d, "all", user_id=user_id)
        slot_counts = self._extract_slot_counts(text)

        self.assertEqual(sum(slot_counts.values()), 2)
        self.assertEqual(sum(1 for count in slot_counts.values() if count > 0), 2)


if __name__ == "__main__":
    unittest.main()
