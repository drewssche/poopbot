from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Chat, ChatMember, PoopEvent, Session as DaySession, SessionUserState, User
from app.services.reminder_service import build_late_reminder_text


class ReminderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_group_late_reminder_ignores_foreign_mirrored_state(self) -> None:
        group_chat_id = -100
        private_chat_id = 7
        user_id = 1
        today = date(2026, 3, 27)

        self.db.add(Chat(chat_id=group_chat_id, timezone="Europe/Minsk", is_enabled=True))
        self.db.add(Chat(chat_id=private_chat_id, timezone="Europe/Minsk", is_enabled=True))
        self.db.add(User(user_id=user_id, username="u1", first_name="U", last_name="1"))
        self.db.add(ChatMember(chat_id=group_chat_id, user_id=user_id))
        group_sess = DaySession(chat_id=group_chat_id, session_date=today, status="active")
        self.db.add(group_sess)
        self.db.flush()

        self.db.add(SessionUserState(session_id=group_sess.session_id, user_id=user_id, poops_n=1))
        self.db.add(
            PoopEvent(
                session_id=group_sess.session_id,
                user_id=user_id,
                event_n=1,
                origin_chat_id=private_chat_id,
            )
        )
        self.db.commit()

        text = build_late_reminder_text(self.db, group_sess.session_id)

        self.assertIsNotNone(text)
        self.assertIn("@u1", text or "")


if __name__ == "__main__":
    unittest.main()
