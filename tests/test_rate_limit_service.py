from __future__ import annotations

from datetime import UTC, datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import RateLimit
from app.services.rate_limit_service import check_rate_limit


class RateLimitServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_allows_first_action(self) -> None:
        allowed = check_rate_limit(self.db, chat_id=1, user_id=2, scope="Q1", cooldown_seconds=2)
        self.assertTrue(allowed)

    def test_handles_existing_naive_timestamp(self) -> None:
        self.db.add(
            RateLimit(
                chat_id=1,
                user_id=2,
                scope="Q1",
                last_action_at=datetime.now(UTC).replace(tzinfo=None, microsecond=0),
            )
        )
        self.db.commit()

        allowed = check_rate_limit(self.db, chat_id=1, user_id=2, scope="Q1", cooldown_seconds=60)

        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
