from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Chat
from app.services.repo_service import migrate_chat_settings


class TestMigrateChatSettings:
    def setup_method(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)
        self.db: Session = self.SessionLocal()

    def teardown_method(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_copies_settings_and_disables_old_chat(self) -> None:
        self.db.add(
            Chat(
                chat_id=-123,
                timezone="Europe/Berlin",
                notifications_enabled=False,
                late_reminder_enabled=False,
                q2_q3_enabled=True,
                is_enabled=True,
                show_in_global=False,
            )
        )
        self.db.commit()

        migrated = migrate_chat_settings(self.db, -123, -1000000000123)
        self.db.commit()

        assert migrated is not None
        old_chat = self.db.get(Chat, -123)
        new_chat = self.db.get(Chat, -1000000000123)

        assert old_chat is not None
        assert new_chat is not None
        assert old_chat.is_enabled is False
        assert new_chat.is_enabled is True
        assert new_chat.timezone == "Europe/Berlin"
        assert new_chat.notifications_enabled is False
        assert new_chat.late_reminder_enabled is False
        assert new_chat.q2_q3_enabled is True
        assert new_chat.show_in_global is False

    def test_returns_none_when_old_chat_is_missing(self) -> None:
        assert migrate_chat_settings(self.db, -1, -1001) is None
