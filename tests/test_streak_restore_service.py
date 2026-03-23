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


if __name__ == "__main__":
    unittest.main()
