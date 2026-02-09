from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import BigInteger, DateTime, String, func, Boolean, ForeignKey, UniqueConstraint, Date

from sqlalchemy.orm import relationship

from app.db.base import Base


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(
        String(32), nullable=False)  # private/supergroup/group
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True)


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(
        "chats.chat_id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)

    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    is_opted_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DailySession(Base):
    __tablename__ = "daily_sessions"
    __table_args__ = (
        UniqueConstraint("chat_id", "day", name="uq_daily_sessions_chat_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(
        "chats.chat_id"), index=True, nullable=False)
    day: Mapped[object] = mapped_column(Date, index=True, nullable=False)

    message1_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message2_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    closed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Q1Answer(Base):
    __tablename__ = "q1_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id",
                         name="uq_q1_answers_session_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    session_id: Mapped[int] = mapped_column(ForeignKey(
        "daily_sessions.id"), index=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)  # для быстрых выборок
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)

    # 'poop' | 'no' | 'later'
    answer: Mapped[str] = mapped_column(String(16), nullable=False)

    # если answer == 'later' — когда напомнить (UTC хранить нормально)
    remind_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Q2Answer(Base):
    __tablename__ = "q2_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id",
                         name="uq_q2_answers_session_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    session_id: Mapped[int] = mapped_column(ForeignKey(
        "daily_sessions.id"), index=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)

    # 'good' | 'ok' | 'bad'
    answer: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("chat_id", "session_id", "user_id",
                         name="uq_reminders_chat_session_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey(
        "daily_sessions.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)

    remind_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False)
    is_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
