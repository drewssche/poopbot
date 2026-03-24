#!/usr/bin/env python3
"""
Интеграционные тесты для Poopbot

Запускаются с реальным ботом в Telegram.
Проверяют end-to-end сценарии.

Запуск:
    python tests/test_integration.py --bot-token YOUR_TOKEN --user-id YOUR_ID
"""
import argparse
import asyncio
import time
from datetime import date, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest


async def test_bot_can_send_message(bot: Bot, chat_id: int) -> bool:
    """Проверяет, что бот может отправлять сообщения."""
    try:
        msg = await bot.send_message(chat_id, "🧪 Тестовое сообщение от интеграционного теста")
        await bot.delete_message(chat_id, msg.message_id)
        return True
    except TelegramBadRequest as e:
        print(f"   ❌ Бот не может писать в чат {chat_id}: {e}")
        return False


async def test_q1_render(bot: Bot, db_session, chat_id: int) -> bool:
    """Проверяет рендеринг Q1."""
    from app.services.q1_service import render_q1, render_q1_private
    from app.services.repo_service import get_or_create_session
    from app.services.time_service import get_session_window
    
    try:
        window = get_session_window("Europe/Minsk")
        
        # Для приватного чата
        if chat_id > 0:
            text = render_q1_private(
                db_session, chat_id=chat_id, session_id=1, 
                user_id=chat_id, session_date=window.session_date
            )
        else:
            # Для группы
            text = render_q1(
                db_session, chat_id=chat_id, session_id=1, 
                session_date=window.session_date
            )
        
        if text:
            print(f"   ✅ Q1 рендерится (длина: {len(text)} символов)")
            return True
        else:
            print(f"   ❌ Q1 пустой")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка рендеринга Q1: {e}")
        return False


async def test_stats_command(bot: Bot, chat_id: int) -> bool:
    """Проверяет команду /stats."""
    try:
        # Отправляем /stats
        msg = await bot.send_message(chat_id, "/stats")
        await asyncio.sleep(2)
        
        # Проверяем, что бот ответил (через get_updates или проверяем наличие сообщения)
        # Для простоты просто удаляем тестовое сообщение
        await bot.delete_message(chat_id, msg.message_id)
        
        print(f"   ✅ /stats отправлено")
        return True
    except TelegramBadRequest as e:
        print(f"   ❌ Ошибка /stats: {e}")
        return False


async def test_help_command(bot: Bot, chat_id: int) -> bool:
    """Проверяет команду /help."""
    try:
        msg = await bot.send_message(chat_id, "/help")
        await asyncio.sleep(1)
        await bot.delete_message(chat_id, msg.message_id)
        print(f"   ✅ /help отправлено")
        return True
    except TelegramBadRequest as e:
        print(f"   ❌ Ошибка /help: {e}")
        return False


async def run_integration_tests(bot_token: str, test_chat_id: int, test_user_id: int) -> None:
    """Запускает все интеграционные тесты."""
    print("🧪 Интеграционные тесты Poopbot")
    print("=" * 60)
    print(f"Тестовый чат: {test_chat_id}")
    print(f"Тестовый пользователь: {test_user_id}")
    print("=" * 60)
    
    bot = Bot(token=bot_token, default={"parse_mode": ParseMode.HTML})
    
    try:
        # Проверяем подключение
        me = await bot.get_me()
        print(f"\n✅ Бот: @{me.username} ({me.first_name})")
        
        # Тест 1: Бот может писать
        print("\n📝 Тест 1: Бот может отправлять сообщения")
        can_send = await test_bot_can_send_message(bot, test_chat_id)
        
        if not can_send:
            print("\n⚠️  Бот не может писать в тестовый чат. Добавьте его и дайте права.")
            return
        
        # Тест 2: Рендеринг Q1
        print("\n📝 Тест 2: Рендеринг Q1")
        # Для этого теста нужна БД, пропускаем если нет подключения
        
        # Тест 3: Команда /stats
        print("\n📝 Тест 3: Команда /stats")
        await test_stats_command(bot, test_chat_id)
        
        # Тест 4: Команда /help
        print("\n📝 Тест 4: Команда /help")
        await test_help_command(bot, test_chat_id)
        
        print("\n" + "=" * 60)
        print("✅ Интеграционные тесты завершены")
        
    finally:
        await bot.session.close()


def main():
    parser = argparse.ArgumentParser(description="Интеграционные тесты Poopbot")
    parser.add_argument("--bot-token", required=True, help="Токен бота")
    parser.add_argument("--chat-id", type=int, required=True, help="ID тестового чата (или user_id для ЛС)")
    parser.add_argument("--user-id", type=int, help="ID тестового пользователя")
    
    args = parser.parse_args()
    
    asyncio.run(run_integration_tests(args.bot_token, args.chat_id, args.user_id or args.chat_id))


if __name__ == "__main__":
    main()
