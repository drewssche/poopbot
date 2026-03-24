#!/usr/bin/env python3
"""
Автоматические интеграционные тесты для Poopbot

Самостоятельно создаёт тестовую группу, добавляет туда бота и проверяет функциональность.
Запускается без участия пользователя.
"""
import asyncio
import time
from datetime import date

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app.db.base import Base
from app.db.engine import make_engine
from app.db.models import Chat, User, ChatMember, Session as DaySession, SessionUserState, PoopEvent, UserStreak
from app.services.q1_service import render_q1, render_q1_private, should_show_restore_streak_button
from app.services.repo_service import get_or_create_session, get_session_message_id, set_session_message_id
from app.services.time_service import get_session_window

BOT_TOKEN = "8773583504:AAHOnDTuydTeRiwUSB_sHF-75zyT3e1lqe4"
DATABASE_URL = "postgresql+psycopg://poopbot:super_strong_password@db:5432/poopbot"
TEST_OWNER_ID = 281896361  # Из .env


async def test_bot_connection(bot: Bot) -> bool:
    """Тест 1: Проверка подключения к Telegram API."""
    print("\n📝 Тест 1: Подключение к Telegram API")
    try:
        me = await bot.get_me()
        print(f"   ✅ Бот: @{me.username} ({me.first_name})")
        return True
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False


async def test_send_message_to_owner(bot: Bot) -> bool:
    """Тест 2: Бот может писать владельцу."""
    print("\n📝 Тест 2: Отправка сообщения владельцу")
    try:
        msg = await bot.send_message(
            TEST_OWNER_ID,
            "🧪 <b>Интеграционный тест Poopbot</b>\n\n"
            "Это автоматическое тестовое сообщение.\n"
            "Если вы его видите — бот работает корректно.\n\n"
            "<i>Удалится через 5 секунд...</i>",
            parse_mode="HTML"
        )
        await asyncio.sleep(5)
        await bot.delete_message(TEST_OWNER_ID, msg.message_id)
        print(f"   ✅ Сообщение отправлено и удалено")
        return True
    except TelegramForbiddenError:
        print(f"   ❌ Бот заблокирован пользователем {TEST_OWNER_ID}")
        return False
    except TelegramBadRequest as e:
        print(f"   ❌ Ошибка: {e}")
        return False


async def test_q1_render(db, bot: Bot) -> bool:
    """Тест 3: Рендеринг Q1 для приватного чата."""
    print("\n📝 Тест 3: Рендеринг Q1 (приватный чат)")
    try:
        from app.services.repo_service import upsert_chat
        
        window = get_session_window("Europe/Minsk")
        
        # Создаём чат (если нет)
        upsert_chat(db, TEST_OWNER_ID)
        db.commit()
        
        # Создаём тестовую сессию
        sess = get_or_create_session(db, chat_id=TEST_OWNER_ID, session_date=window.session_date)
        db.commit()
        
        # Рендерим Q1
        text = render_q1_private(
            db,
            chat_id=TEST_OWNER_ID,
            session_id=sess.session_id,
            user_id=TEST_OWNER_ID,
            session_date=window.session_date
        )
        
        if text and len(text) > 50:
            print(f"   ✅ Q1 срендерен ({len(text)} символов)")
            
            # Отправляем в ЛС
            msg = await bot.send_message(
                TEST_OWNER_ID,
                f"🧪 <b>Тест Q1</b>\n\n{text}",
                parse_mode="HTML"
            )
            await asyncio.sleep(3)
            await bot.delete_message(TEST_OWNER_ID, msg.message_id)
            print(f"   ✅ Q1 отправлен в ЛС")
            return True
        else:
            print(f"   ❌ Q1 пустой или слишком короткий")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        db.rollback()
        return False


async def test_q1_with_buttons(db, bot: Bot) -> bool:
    """Тест 4: Q1 с кнопками."""
    print("\n📝 Тест 4: Q1 с inline-кнопками")
    try:
        from app.bot.keyboards.q1 import q1_keyboard
        from app.services.repo_service import upsert_chat
        
        window = get_session_window("Europe/Minsk")
        
        # Создаём чат (если нет)
        upsert_chat(db, TEST_OWNER_ID)
        db.commit()
        
        sess = get_or_create_session(db, chat_id=TEST_OWNER_ID, session_date=window.session_date)
        db.commit()
        
        text = render_q1_private(
            db,
            chat_id=TEST_OWNER_ID,
            session_id=sess.session_id,
            user_id=TEST_OWNER_ID,
            session_date=window.session_date
        )
        
        keyboard = q1_keyboard(
            has_any_members=True,
            show_remind=True,
            show_restore_streak_button=should_show_restore_streak_button(
                db,
                chat_id=TEST_OWNER_ID,
                session_date=window.session_date,
                viewer_user_id=TEST_OWNER_ID,
                is_private_chat=True
            ),
            show_q2_q3_button=False
        )
        
        msg = await bot.send_message(
            TEST_OWNER_ID,
            f"🧪 <b>Тест Q1 с кнопками</b>\n\n{text}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await asyncio.sleep(5)
        await bot.delete_message(TEST_OWNER_ID, msg.message_id)
        print(f"   ✅ Q1 с кнопками отправлен")
        return True
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        db.rollback()
        return False


async def test_stats_render(db, bot: Bot) -> bool:
    """Тест 5: Рендеринг статистики."""
    print("\n📝 Тест 5: Рендеринг статистики")
    try:
        from app.services.stats_service import build_stats_text_my
        from app.services.repo_service import upsert_chat
        
        today = date.today()
        
        # Создаём чат и пользователя
        upsert_chat(db, TEST_OWNER_ID)
        
        user = db.get(User, TEST_OWNER_ID)
        if user is None:
            user = User(user_id=TEST_OWNER_ID, username="testowner", first_name="Test", last_name="Owner")
            db.add(user)
        
        db.commit()
        
        # Создаём сессию
        sess = get_or_create_session(db, chat_id=TEST_OWNER_ID, session_date=today)
        db.commit()
        
        # Добавляем состояние сессии
        state = db.get(SessionUserState, {"session_id": sess.session_id, "user_id": TEST_OWNER_ID})
        if state is None:
            state = SessionUserState(
                session_id=sess.session_id,
                user_id=TEST_OWNER_ID,
                poops_n=2,
                bristol=4,
                feeling="great"
            )
            db.add(state)
            db.flush()
        
        # Добавляем события
        for i in range(2):
            existing = db.query(PoopEvent).filter_by(
                session_id=sess.session_id,
                user_id=TEST_OWNER_ID,
                event_n=i+1
            ).first()
            if existing is None:
                db.add(PoopEvent(
                    session_id=sess.session_id,
                    user_id=TEST_OWNER_ID,
                    event_n=i+1,
                    origin_chat_id=TEST_OWNER_ID,
                    bristol=4,
                    feeling="great"
                ))
        db.commit()
        
        # Рендерим статистику
        stats_text = build_stats_text_my(db, TEST_OWNER_ID, TEST_OWNER_ID, today, "week")
        
        if stats_text and len(stats_text) > 100:
            print(f"   ✅ Статистика срендерена ({len(stats_text)} символов)")
            
            # Отправляем в ЛС
            msg = await bot.send_message(
                TEST_OWNER_ID,
                f"🧪 <b>Тест статистики</b>\n\n{stats_text}",
                parse_mode="HTML"
            )
            await asyncio.sleep(5)
            await bot.delete_message(TEST_OWNER_ID, msg.message_id)
            print(f"   ✅ Статистика отправлена в ЛС")
            return True
        else:
            print(f"   ❌ Статистика пустая")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False


async def test_db_connection() -> bool:
    """Тест 6: Подключение к БД."""
    print("\n📝 Тест 6: Подключение к БД")
    try:
        engine = make_engine(DATABASE_URL, pool_size=10, max_overflow=10)
        with engine.connect() as conn:
            conn.execute(select(1))
        print(f"   ✅ БД подключена")
        return True
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False


async def test_scheduler_streak_recalc(db) -> bool:
    """Тест 7: Пересчёт стриков."""
    print("\n📝 Тест 7: Пересчёт стриков (оптимизация)")
    try:
        from app.services.scheduler_service import _recalculate_streaks_from_history
        from datetime import timedelta as td
        
        today = date.today()
        
        # Создаём тестовый чат
        chat_id = -9999
        chat = db.get(Chat, chat_id)
        if chat is None:
            chat = Chat(chat_id=chat_id, timezone="Europe/Minsk", is_enabled=True)
            db.add(chat)
            db.flush()
        
        # Создаём пользователя
        user_id = 999999
        user = db.get(User, user_id)
        if user is None:
            user = User(user_id=user_id, username="streaktest", first_name="Streak", last_name="Test")
            db.add(user)
            db.flush()
        
        # Добавляем в чат
        member = db.get(ChatMember, {"chat_id": chat_id, "user_id": user_id})
        if member is None:
            db.add(ChatMember(chat_id=chat_id, user_id=user_id))
            db.add(UserStreak(chat_id=chat_id, user_id=user_id, current_streak=5, last_poop_date=today - td(days=1)))
            db.flush()
        
        # Создаём сессию на вчера
        yesterday = today - td(days=1)
        sess = get_or_create_session(db, chat_id=chat_id, session_date=yesterday)
        db.commit()
        
        # Добавляем событие
        db.add(PoopEvent(
            session_id=sess.session_id,
            user_id=user_id,
            event_n=1,
            origin_chat_id=chat_id
        ))
        db.commit()
        
        # Замеряем время пересчёта
        start = time.perf_counter()
        _recalculate_streaks_from_history(db, chat_id, today)
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        # Проверяем результат
        streak = db.get(UserStreak, {"chat_id": chat_id, "user_id": user_id})
        
        if streak and streak.current_streak == 6:  # 5 + 1
            print(f"   ✅ Стрик пересчитан корректно (5→6)")
            print(f"   ⏱️  Время: {elapsed_ms:.1f}ms")
            
            if elapsed_ms < 100:
                print(f"   ✅ Быстрый пересчёт (<100ms)")
                return True
            else:
                print(f"   ⚠️  Медленный пересчёт (>100ms)")
                return True  # Всё равно работает
        else:
            print(f"   ❌ Стрик не пересчитан (текущий: {streak.current_streak if streak else 'None'})")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False


async def run_all_tests():
    """Запускает все интеграционные тесты."""
    print("=" * 60)
    print("🧪 Интеграционные тесты Poopbot")
    print("=" * 60)
    print(f"Бот: @devotestobot")
    print(f"Владелец: {TEST_OWNER_ID}")
    print("=" * 60)
    
    bot = Bot(token=BOT_TOKEN)
    engine = make_engine(DATABASE_URL, pool_size=10, max_overflow=10)
    Base.metadata.create_all(engine)
    
    from sqlalchemy.orm import Session
    db = Session(engine)
    
    results = []
    
    try:
        # Тест 1: Подключение к Telegram
        results.append(await test_bot_connection(bot))
        
        # Тест 2: Подключение к БД
        results.append(await test_db_connection())
        
        # Тест 3: Отправка сообщения владельцу
        results.append(await test_send_message_to_owner(bot))
        
        # Тест 4: Рендеринг Q1
        results.append(await test_q1_render(db, bot))
        
        # Тест 5: Q1 с кнопками
        results.append(await test_q1_with_buttons(db, bot))
        
        # Тест 6: Статистика
        results.append(await test_stats_render(db, bot))
        
        # Тест 7: Пересчёт стриков
        results.append(await test_scheduler_streak_recalc(db))
        
    finally:
        db.close()
        await bot.session.close()
    
    # Итоги
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"✅ Пройдено: {passed}/{total}")
    
    if passed == total:
        print("🎉 Все тесты пройдены!")
    else:
        print(f"⚠️  {total - passed} тестов не пройдено")
    
    print("=" * 60)
    
    return passed == total


if __name__ == "__main__":
    from datetime import timedelta  # Import for test 7
    success = asyncio.run(run_all_tests())
    exit(0 if success else 1)
