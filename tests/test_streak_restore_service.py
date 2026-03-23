from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Chat
from app.services.command_message_service import get_command_message_id
from app.services.streak_restore_service import (
    STREAK_RESTORE_INCIDENT_COMMAND,
    detect_suspected_streak_incident_dates,
    list_active_group_chat_ids,
    send_streak_restore_battle_message,
    send_streak_restore_incident_message_to_chat,
    send_streak_restore_incident_messages,
)


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message_id=len(self.calls))


class StreakRestoreServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)
        self.db: Session = self.SessionLocal()

        self.db.add(Chat(chat_id=-100, timezone="Europe/Minsk", is_enabled=True))
        self.db.add(Chat(chat_id=-200, timezone="Europe/Minsk", is_enabled=True))
        self.db.add(Chat(chat_id=42, timezone="Europe/Minsk", is_enabled=True))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    async def test_sends_incident_message_once_to_all_active_groups(self) -> None:
        bot = _FakeBot()
        await send_streak_restore_incident_messages(
            bot,
            self.SessionLocal,
            target_date=date(2026, 3, 22),
            scope="groups",
            chat_throttle_sec=0,
        )

        self.assertEqual([call["chat_id"] for call in bot.calls], [-200, -100])
        with self.SessionLocal() as db:
            self.assertIsNotNone(
                get_command_message_id(db, -100, 0, STREAK_RESTORE_INCIDENT_COMMAND, date(2026, 3, 22))
            )
            self.assertIsNone(
                get_command_message_id(db, 42, 0, STREAK_RESTORE_INCIDENT_COMMAND, date(2026, 3, 22))
            )

    async def test_does_not_duplicate_message_on_second_startup(self) -> None:
        bot = _FakeBot()
        await send_streak_restore_incident_messages(
            bot,
            self.SessionLocal,
            target_date=date(2026, 3, 22),
            scope="groups",
            chat_throttle_sec=0,
        )
        await send_streak_restore_incident_messages(
            bot,
            self.SessionLocal,
            target_date=date(2026, 3, 22),
            scope="groups",
            chat_throttle_sec=0,
        )

        self.assertEqual(len(bot.calls), 2)

    async def test_detects_single_missing_day_as_top_candidate(self) -> None:
        with self.SessionLocal() as db:
            from app.db.models import PoopEvent, Session as DaySession

            sessions: dict[tuple[int, date], int] = {}
            for chat_id in (-100, -200):
                for session_date in (date(2026, 3, 21), date(2026, 3, 23)):
                    sess = DaySession(chat_id=chat_id, session_date=session_date, status="closed")
                    db.add(sess)
                    db.flush()
                    sessions[(chat_id, session_date)] = int(sess.session_id)

            for chat_id, user_id in [(-100, 10), (-100, 11), (-200, 20)]:
                for session_date in (date(2026, 3, 21), date(2026, 3, 23)):
                    db.add(
                        PoopEvent(
                            session_id=sessions[(chat_id, session_date)],
                            user_id=user_id,
                            event_n=1,
                            origin_chat_id=chat_id,
                        )
                    )
            db.commit()

            candidates = detect_suspected_streak_incident_dates(db, today=date(2026, 3, 24))

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["date"], "2026-03-22")
        self.assertEqual(candidates[0]["total"], 3)

    async def test_detects_yesterday_as_candidate_even_without_next_day_activity(self) -> None:
        with self.SessionLocal() as db:
            from app.db.models import PoopEvent, Session as DaySession

            sessions: dict[tuple[int, date], int] = {}
            for chat_id in (-100, -200):
                session_date = date(2026, 3, 21)
                sess = DaySession(chat_id=chat_id, session_date=session_date, status="closed")
                db.add(sess)
                db.flush()
                sessions[(chat_id, session_date)] = int(sess.session_id)

            for chat_id, user_id in [(-100, 10), (-100, 11), (-200, 20)]:
                db.add(
                    PoopEvent(
                        session_id=sessions[(chat_id, date(2026, 3, 21))],
                        user_id=user_id,
                        event_n=1,
                        origin_chat_id=chat_id,
                    )
                )
            db.commit()

            candidates = detect_suspected_streak_incident_dates(db, today=date(2026, 3, 23))

        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["date"], "2026-03-22")
        self.assertEqual(candidates[0]["total"], 3)

    async def test_battle_message_uses_real_restore_callback(self) -> None:
        bot = _FakeBot()
        await send_streak_restore_battle_message(bot, owner_chat_id=42, target_date=date(2026, 3, 22))

        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0]["chat_id"], 42)
        self.assertIn("последние 7 дней", bot.calls[0]["text"])
        markup = bot.calls[0]["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "restore:claim")

    async def test_lists_only_active_groups(self) -> None:
        with self.SessionLocal() as db:
            self.assertEqual(list_active_group_chat_ids(db), [-200, -100])

    async def test_sends_incident_message_to_one_selected_group_once(self) -> None:
        bot = _FakeBot()
        first = await send_streak_restore_incident_message_to_chat(
            bot,
            self.SessionLocal,
            chat_id=-100,
            target_date=date(2026, 3, 22),
        )
        second = await send_streak_restore_incident_message_to_chat(
            bot,
            self.SessionLocal,
            chat_id=-100,
            target_date=date(2026, 3, 22),
        )

        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(first["sent"], 1)
        self.assertFalse(first["duplicate"])
        self.assertEqual(second["skipped"], 1)
        self.assertTrue(second["duplicate"])

    async def test_force_resend_selected_group_overwrites_mapping_and_sends_again(self) -> None:
        bot = _FakeBot()
        await send_streak_restore_incident_message_to_chat(
            bot,
            self.SessionLocal,
            chat_id=-100,
            target_date=date(2026, 3, 22),
        )
        resent = await send_streak_restore_incident_message_to_chat(
            bot,
            self.SessionLocal,
            chat_id=-100,
            target_date=date(2026, 3, 22),
            force=True,
        )

        self.assertEqual(len(bot.calls), 2)
        self.assertEqual(resent["sent"], 1)
        self.assertFalse(resent["duplicate"])
        with self.SessionLocal() as db:
            self.assertEqual(
                get_command_message_id(db, -100, 0, STREAK_RESTORE_INCIDENT_COMMAND, date(2026, 3, 22)),
                2,
            )

    async def test_force_resend_all_groups_ignores_existing_dedup(self) -> None:
        bot = _FakeBot()
        await send_streak_restore_incident_messages(
            bot,
            self.SessionLocal,
            target_date=date(2026, 3, 22),
            scope="groups",
            chat_throttle_sec=0,
        )
        resent = await send_streak_restore_incident_messages(
            bot,
            self.SessionLocal,
            target_date=date(2026, 3, 22),
            scope="groups",
            chat_throttle_sec=0,
            force=True,
        )

        self.assertEqual(len(bot.calls), 4)
        self.assertEqual(resent["sent"], 2)
        self.assertEqual(resent["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
