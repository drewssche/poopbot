"""Render synthetic examples; send only to the configured owner when explicitly requested."""
import argparse
import asyncio
from datetime import date
import json
from pathlib import Path

from app.services.stats_common import period_to_range
from app.services.weekly_report_card import Participant, ReportCard, WeeklyDetails, render_card


def samples():
    for period, total in (('week', 21), ('month', 210), ('year', 3000)):
        span = period_to_range(date(2026, 10, 19), period)
        length = (span.end - span.start).days + 1
        days = tuple(total // length + (n < total % length) for n in range(length))
        participants = tuple(Participant(f'Участник {n+1}', total // 3) for n in range(3))
        details = WeeklyDetails(18, 6, (0, 0, 0, 0, total), (0, 0, 0, total)) if period == 'week' else None
        yield ReportCard(span.start, span.end, 'Демонстрационные данные', days, participants,
                         label='Демо · пример', period=period,
                         layout='weekly' if period == 'week' else 'summary', details=details)


async def main(output, send):
    output.mkdir(parents=True, exist_ok=True)
    bot = None
    if send:
        from aiogram import Bot
        from app.core.config import load_settings
        settings = load_settings()
        if not settings.bot_owner_id or settings.bot_owner_id <= 0:
            raise RuntimeError('A configured owner DM is required')
        bot = Bot(settings.bot_token)
    try:
        for card in samples():
            picture = render_card(card)
            (output / f'{card.period}.png').write_bytes(picture)
            if bot:
                from aiogram.types import BufferedInputFile
                period = {'week': 'Недельный', 'month': 'Месячный', 'year': 'Годовой'}[card.period]
                message = await bot.send_photo(settings.bot_owner_id,
                    BufferedInputFile(picture, f'{card.period}-example.png'),
                    caption=f'{period} отчёт — образец на вымышленных данных. '
                            'Показываю новый цвет воды и сравнение массы с животным. '
                            'Эти числа не относятся к вашей истории.', request_timeout=30)
                print(json.dumps({'period': card.period, 'message_id': message.message_id}), flush=True)
                await asyncio.sleep(1)
    finally:
        if bot:
            await bot.session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('/tmp/poopbot-animal-examples'))
    parser.add_argument('--send-to-owner', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.output, args.send_to_owner))
