from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.bot.keyboards.streak_restore import streak_restore_keyboard, streak_restore_preview_keyboard
from app.db.models import Chat, PoopEvent, Session as DaySession
from app.db.session import db_session
from app.services.command_message_service import get_command_message_id, set_command_message_id
from app.services.scheduler_telegram import safe_send_message

logger = logging.getLogger(__name__)

STREAK_RESTORE_INCIDENT_COMMAND = "streak_restore_incident"


def streak_restore_message_text(target_date: date) -> str:
    return (
        "⚠️ Из-за сбоя бот мог пропустить входящие сообщения.\n\n"
        f"Если у тебя сломался стрик из-за пропуска за {target_date.strftime('%d.%m.%Y')}, "
        "нажми кнопку ниже. Бот проверит, можно ли восстановить день именно тебе."
    )


def streak_restore_preview_text(target_date: date) -> str:
    return (
        "👀 Превью сообщения восстановления\n\n"
        f"Дата инцидента: {target_date.strftime('%d.%m.%Y')}\n\n"
        "Ниже показано, как будет выглядеть сообщение в чате. "
        "Кнопка в превью не восстанавливает стрик."
    )


def detect_suspected_streak_incident_dates(db, *, today: date, lookback_days: int = 21, limit: int = 5) -> list[dict[str, int | str]]:
    start_date = today - timedelta(days=max(3, lookback_days))
    rows = db.execute(
        select(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .join(PoopEvent, PoopEvent.session_id == DaySession.session_id)
        .where(
            DaySession.session_date >= start_date,
            DaySession.session_date <= today,
            PoopEvent.origin_chat_id == DaySession.chat_id,
        )
        .group_by(DaySession.chat_id, PoopEvent.user_id, DaySession.session_date)
        .order_by(DaySession.chat_id.asc(), PoopEvent.user_id.asc(), DaySession.session_date.asc())
    ).all()

    days_by_actor: dict[tuple[int, int], list[date]] = {}
    for chat_id, user_id, session_date in rows:
        days_by_actor.setdefault((int(chat_id), int(user_id)), []).append(session_date)

    counters: dict[date, dict[str, int]] = {}
    for (chat_id, _user_id), days in days_by_actor.items():
        for idx in range(1, len(days)):
            prev_day = days[idx - 1]
            next_day = days[idx]
            if next_day - prev_day != timedelta(days=2):
                continue
            missing_day = prev_day + timedelta(days=1)
            if missing_day >= today:
                continue
            bucket = counters.setdefault(missing_day, {"total": 0, "groups": 0, "private": 0})
            bucket["total"] += 1
            if chat_id < 0:
                bucket["groups"] += 1
            else:
                bucket["private"] += 1

    # Fresh incidents are under-observed by the exact-gap heuristic because
    # there may be no D+1 data yet. Add a softer candidate for yesterday when
    # users had activity on D-1 but still have no mark on D.
    recent_target = today - timedelta(days=1)
    if recent_target > start_date:
        recent_prev = recent_target - timedelta(days=1)
        for (chat_id, _user_id), days in days_by_actor.items():
            day_set = set(days)
            if recent_prev not in day_set or recent_target in day_set:
                continue
            bucket = counters.setdefault(recent_target, {"total": 0, "groups": 0, "private": 0})
            bucket["total"] += 1
            if chat_id < 0:
                bucket["groups"] += 1
            else:
                bucket["private"] += 1

    ranked = sorted(
        (
            {
                "date": day.isoformat(),
                "total": counts["total"],
                "groups": counts["groups"],
                "private": counts["private"],
            }
            for day, counts in counters.items()
            if counts["total"] > 0
        ),
        key=lambda item: (-int(item["total"]), str(item["date"])),
    )
    return ranked[:limit]


def collect_streak_restore_incident_stats(db, *, target_date: date) -> dict[str, int]:
    group_rows = list(
        db.scalars(
            select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id < 0).order_by(Chat.chat_id.asc())
        ).all()
    )
    private_rows = list(
        db.scalars(
            select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id > 0).order_by(Chat.chat_id.asc())
        ).all()
    )
    return {
        "groups_sent": sum(
            1
            for chat_id in group_rows
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None
        ),
        "private_sent": sum(
            1
            for chat_id in private_rows
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None
        ),
    }


def list_active_group_chat_ids(db) -> list[int]:
    return list(
        int(chat_id)
        for chat_id in db.scalars(
            select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id < 0).order_by(Chat.chat_id.asc())
        ).all()
    )


async def send_streak_restore_preview_message(bot: Bot, *, owner_chat_id: int, target_date: date) -> int:
    sent = await safe_send_message(
        bot,
        chat_id=owner_chat_id,
        text=streak_restore_preview_text(target_date),
        reply_markup=streak_restore_preview_keyboard(target_date.isoformat()),
    )
    return int(sent.message_id)


async def send_streak_restore_battle_message(bot: Bot, *, owner_chat_id: int, target_date: date) -> int:
    sent = await safe_send_message(
        bot,
        chat_id=owner_chat_id,
        text=streak_restore_message_text(target_date),
        reply_markup=streak_restore_keyboard(target_date.isoformat()),
    )
    return int(sent.message_id)


async def send_streak_restore_incident_message_to_chat(
    bot: Bot,
    session_factory: sessionmaker,
    *,
    chat_id: int,
    target_date: date,
) -> dict[str, int | bool]:
    with db_session(session_factory) as db:
        if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None:
            return {"sent": 0, "skipped": 1, "failed": 0, "duplicate": True}
        try:
            sent = await safe_send_message(
                bot,
                chat_id=int(chat_id),
                text=streak_restore_message_text(target_date),
                reply_markup=streak_restore_keyboard(target_date.isoformat()),
            )
            set_command_message_id(
                db,
                int(chat_id),
                0,
                STREAK_RESTORE_INCIDENT_COMMAND,
                target_date,
                sent.message_id,
            )
            return {"sent": 1, "skipped": 0, "failed": 0, "duplicate": False}
        except Exception:
            logger.exception("Failed to send streak restore incident message chat_id=%s", chat_id)
            return {"sent": 0, "skipped": 0, "failed": 1, "duplicate": False}


async def send_streak_restore_incident_messages(
    bot: Bot,
    session_factory: sessionmaker,
    *,
    target_date: date,
    scope: str,
    chat_throttle_sec: float = 0.2,
) -> dict[str, int]:
    if scope not in {"groups", "private"}:
        raise ValueError(f"Unsupported scope: {scope}")

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    with db_session(session_factory) as db:
        if scope == "groups":
            chat_ids = list_active_group_chat_ids(db)
        else:
            chat_ids = list(
                db.scalars(
                    select(Chat.chat_id).where(Chat.is_enabled == True, Chat.chat_id > 0).order_by(Chat.chat_id.asc())
                ).all()
            )

    for chat_id in chat_ids:
        with db_session(session_factory) as db:
            if get_command_message_id(db, int(chat_id), 0, STREAK_RESTORE_INCIDENT_COMMAND, target_date) is not None:
                skipped_count += 1
                continue
            try:
                sent = await safe_send_message(
                    bot,
                    chat_id=int(chat_id),
                    text=streak_restore_message_text(target_date),
                    reply_markup=streak_restore_keyboard(target_date.isoformat()),
                )
                set_command_message_id(
                    db,
                    int(chat_id),
                    0,
                    STREAK_RESTORE_INCIDENT_COMMAND,
                    target_date,
                    sent.message_id,
                )
                sent_count += 1
            except Exception:
                logger.exception("Failed to send streak restore incident message chat_id=%s scope=%s", chat_id, scope)
                failed_count += 1
        if chat_throttle_sec > 0:
            import asyncio

            await asyncio.sleep(chat_throttle_sec)

    return {"sent": sent_count, "skipped": skipped_count, "failed": failed_count}
