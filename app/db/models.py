from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import (
    BigInteger,
    DateTime,
    String,
    func,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Date,
    Integer,
    SmallInteger,
)

from app.db.base import Base


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(
        String(32), nullable=False)  # private/supergroup/group
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True)

    # Настройка времени ежедневного вопроса для чата (по TZ чата).
    # По умолчанию: утро = 10:00
    daily_post_hour: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=10)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id",
                         name="uq_participants_chat_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(
        "chats.chat_id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False)

    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    is_opted_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)

    # Текущий стрик: растёт только если итог дня count>0, иначе сбрасывается
    current_streak_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    current_streak_start: Mapped[object |
                                 None] = mapped_column(Date, nullable=True)

    # Лучший стрик за всё время (по этому чату)
    best_streak_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    best_streak_start: Mapped[object |
                              None] = mapped_column(Date, nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    # под Q3 зарезервируем заранее
    message3_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    closed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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

    # Счётчик 💩 за день
    poop_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0)

    # Напоминание "в конце дня" (22:00 по TZ чата). Храним remind_at в UTC.
    remind_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Для антиспама (можно ограничивать клики раз в N секунд)
    last_action_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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

    # Bristol 1..7
    bristol: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Q3Answer(Base):
    __tablename__ = "q3_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id",
                         name="uq_q3_answers_session_user"),
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
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
