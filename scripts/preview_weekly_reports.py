"""Send explicit owner-only calendar report samples; never mutate operational marks."""
import argparse
import asyncio
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BufferedInputFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import load_settings
from app.db.base import Base
from app.db.models import Chat, PoopEvent, Session as DaySession, User, WeeklyReport
from app.services.weekly_report_caption import build_caption
from app.services.stats_common import period_to_range, previous_period_range
from app.services.weekly_report_card import project_card, render_card
from app.services.weekly_reports import ReportWorker, enqueue_report


def worker_caption(factory, rid, explanation):
    with factory() as db:
        row = db.get(WeeklyReport, rid)
        card = project_card(db, row.source_chat_id, row.period_start, row.period_end,
                            row.chat_title, row.label, period=row.report_period)
        caption = build_caption(db, row.source_chat_id, card)
    value = caption + '\n\n' + explanation
    return value if len(value.encode('utf-16-le'))//2 <= 1024 else caption


async def main(source_chat, period_kind='week', preview_only=False):
    settings = load_settings()
    if not settings.bot_owner_id or settings.bot_owner_id <= 0 or source_chat >= 0:
        raise RuntimeError('An owner DM and an explicit source group are required')
    owner = settings.bot_owner_id
    operational = create_engine(settings.database_url, isolation_level='REPEATABLE READ')
    demo = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(demo)
    factory = sessionmaker(demo, expire_on_commit=False, autoflush=False)
    results = []
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode='HTML'))
    try:
        if not preview_only:
            await bot.send_message(owner, 'Проверка: карточка и итоги в одном сообщении. Первые три — снимки реальных данных '
                '(личка, группа и превью текущего периода). Затем четыре демонстрации обновления той же карточки: '
                'добавление, редактирование, удаление и восстановление. Демонстрационные отметки существуют '
                'только в памяти этого теста и не меняют историю бота.')
        group = await bot.get_chat(source_chat)
        samples = [(source_chat, 'Тест · превью', True)] if preview_only else [
            (owner, 'Тест · личный', False), (source_chat, 'Тест · групповой', False),
            (source_chat, 'Тест · превью', True)]
        for cid, label, preview in samples:
            with operational.connect() as conn:
                from sqlalchemy.orm import Session
                with Session(conn) as db:
                    chat = db.get(Chat, cid)
                    if chat is None:
                        raise RuntimeError('Source chat is missing')
                    today = datetime.now(ZoneInfo(chat.timezone)).date()
                    anchor = today if preview else previous_period_range(today, period_kind).end
                    period = period_to_range(anchor, period_kind)
                    card = project_card(db, cid, period.start, period.end,
                                        group.title if cid < 0 else 'Личные отметки', label,
                                        period=period_kind)
                    caption = build_caption(db, cid, card)
            sent = await bot.send_photo(owner, BufferedInputFile(render_card(card), f'{period_kind}-report.png'),
                                        caption=caption, parse_mode=None)
            results.append({'scenario': label, 'photo': sent.message_id, 'total': card.total})
        if preview_only:
            print(json.dumps(results, ensure_ascii=False))
            return
        worker = ReportWorker(bot, factory, 'unused')
        for index, (kind, explanation) in enumerate([
            ('добавление', 'Добавление: было 1, стало 2 отметки. Появился жёлтый счётчик.'),
            ('редактирование', 'Редактирование: количество осталось 1. Консистенция и самочувствие изменены в подписи.'),
            ('удаление', 'Удаление последней отметки: было 1, стало 0. День снова пустой.'),
            ('восстановление', 'Восстановление пропущенного дня: было 0, стало 1. День появился в календаре.'),
        ]):
            cid = -800-index
            start = previous_period_range(today, period_kind).start
            with factory.begin() as db:
                db.add(Chat(chat_id=cid)); db.add(User(user_id=index+1, first_name='Демо-участник'))
                db.flush()
                session = DaySession(chat_id=cid, session_date=start, status='closed')
                db.add(session); db.flush(); sid = session.session_id
                event_id = None
                if kind != 'восстановление':
                    mark = PoopEvent(session_id=sid, user_id=index+1, origin_chat_id=cid,
                                     event_n=1, bristol=3, feeling='ok', source='recorded')
                    db.add(mark); db.flush(); event_id = mark.id
                row = enqueue_report(db, cid, start, period=period_kind, destination=owner,
                    label=f'Тест · {kind}', chat_title='Демонстрационные данные')
                row.delivery_key = f'sample:{index}'; rid = row.id
            await worker.deliver(rid)
            before = worker.get(rid)
            if before.delivered_revision != before.revision:
                raise RuntimeError(f'Demo delivery failed: {kind}')
            await asyncio.sleep(1)
            with factory.begin() as db:
                if kind in ('добавление', 'восстановление'):
                    db.add(PoopEvent(session_id=sid, user_id=index+1, origin_chat_id=cid,
                        event_n=2 if kind == 'добавление' else 1, bristol=None, feeling=None,
                        source='restore' if kind == 'восстановление' else 'recorded'))
                elif kind == 'редактирование':
                    mark = db.get(PoopEvent, event_id); mark.bristol = 7; mark.feeling = 'bad'
                else:
                    db.delete(db.get(PoopEvent, event_id))
                db.get(WeeklyReport, rid).revision += 1  # SQLite demo only; PG triggers are tested separately.
            await worker.deliver(rid)
            after = worker.get(rid)
            if after.delivered_revision != after.revision or after.photo_message_id != before.photo_message_id:
                raise RuntimeError(f'Demo refresh failed: {kind}')
            await bot.edit_message_caption(chat_id=owner, message_id=after.photo_message_id,
                                           caption=worker_caption(factory, rid, explanation), parse_mode=None)
            results.append({'scenario': kind, 'photo': after.photo_message_id,
                            'same_message': True, 'image_changed': before.card_hash != after.card_hash})
        await bot.send_message(owner, 'Проверка завершена: 7 карточек. В четырёх демонстрациях обновились '
            'прежние сообщения, новых копий при изменении отметок не создавалось. '
            'При редактировании качества отметки изменилась только подпись — количество на карточке осталось прежним. '
            'Эти тестовые снимки больше автоматически обновляться не будут.')
        print(json.dumps(results, ensure_ascii=False))
    finally:
        await bot.session.close()
        operational.dispose(); demo.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-chat', required=True, type=int)
    parser.add_argument('--send-to-owner', required=True, action='store_true', help='Explicitly send samples to BOT_OWNER_ID only')
    parser.add_argument('--period', choices=['week', 'month', 'year'], default='week')
    parser.add_argument('--preview-only', action='store_true', help='Send just one current-period group snapshot')
    args = parser.parse_args()
    asyncio.run(main(args.source_chat, args.period, args.preview_only))
