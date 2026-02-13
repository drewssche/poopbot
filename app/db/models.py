from __future__ import annotations

from datetime import datetime, date, time
from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Integer,
    String, Text, Time, UniqueConstraint, PrimaryKeyConstraint
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Minsk")
    post_time: Mapped[time] = mapped_column(Time, default=time(10, 0))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_global: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    help_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    help_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (PrimaryKeyConstraint("chat_id", "user_id"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("chat_id", "session_date", name="uq_chat_session_date"),)

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    session_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(
        Enum("active", "closed", name="session_status"),
        default="active",
        nullable=False,
    )

    start_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reminded_22_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class SessionMessage(Base):
    __tablename__ = "session_messages"
    __table_args__ = (PrimaryKeyConstraint("session_id", "kind"),)

    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("sessions.session_id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Enum("Q1", "Q2", "Q3", name="message_kind"))
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UserStreak(Base):
    __tablename__ = "user_streaks"
    __table_args__ = (PrimaryKeyConstraint("chat_id", "user_id"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_poop_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class SessionUserState(Base):
    __tablename__ = "session_user_state"
    __table_args__ = (PrimaryKeyConstraint("session_id", "user_id"),)

    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("sessions.session_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))

    poops_n: Mapped[int] = mapped_column(Integer, default=0)
    achievement_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    remind_22: Mapped[bool] = mapped_column(Boolean, default=False)

    bristol: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..7
    feeling: Mapped[str | None] = mapped_column(
        Enum("great", "ok", "bad", name="feeling_kind"),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PoopEvent(Base):
    __tablename__ = "poop_events"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id", "event_n", name="uq_poop_event_per_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    event_n: Mapped[int] = mapped_column(Integer, nullable=False)
    bristol: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..7
    feeling: Mapped[str | None] = mapped_column(
        Enum("great", "ok", "bad", name="feeling_kind"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    __table_args__ = (PrimaryKeyConstraint("chat_id", "user_id", "scope"),)

    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[str] = mapped_column(String(32))
    last_action_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommandMessage(Base):
    __tablename__ = "command_messages"
    __table_args__ = (PrimaryKeyConstraint("chat_id", "user_id", "command", "session_date"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    command: Mapped[str] = mapped_column(String(32))  # e.g. "stats"
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
