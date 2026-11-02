"""Render synthetic examples; send only to the configured owner when explicitly requested."""
import argparse
import asyncio
from datetime import date
import json
from pathlib import Path

from app.services.stats_common import period_to_range
from app.services.weekly_report_card import Participant, ReportCard, WeeklyDetails, render_card


def samples(participant_count=3):
    if not 1 <= participant_count <= 201:
        raise ValueError('Use 1–201 participants for layout previews')
    for period, total in (('week', 21), ('month', 210), ('year', 3000)):
        # Distinct personal totals make animal selection and dense grids reviewable.
        counts = [9, 8, 4] if participant_count == 3 else list(range(participant_count, 0, -1))
        scale = {'week': 1, 'month': 10, 'year': 100}[period]
        counts = [n * scale for n in counts]
        total = sum(counts)
        span = period_to_range(date(2026, 10, 19), period)
        length = (span.end - span.start).days + 1
        days = tuple(total // length + (n < total % length) for n in range(length))
        participants = tuple(Participant(f'Участник {n+1}', count) for n, count in enumerate(counts))
        details = WeeklyDetails(18, 6, (0, 0, 0, 0, total), (0, 0, 0, total)) if period == 'week' else None
        yield ReportCard(span.start, span.end, 'Демонстрационные данные', days, participants,
                         label='Демо · пример', period=period,
                         layout='weekly' if period == 'week' else 'summary', details=details)


async def main(output, send, participant_count=3):
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
        for card in samples(participant_count):
            picture = render_card(card)
            (output / f'{card.period}.png').write_bytes(picture)
            if bot:
                from aiogram.types import BufferedInputFile
                period = {'week': 'Недельный', 'month': 'Месячный', 'year': 'Годовой'}[card.period]
                message = await bot.send_photo(settings.bot_owner_id,
                    BufferedInputFile(picture, f'{card.period}-example.png'),
                    caption=f'{period} отчёт — образец на вымышленных данных. '
                            'Показываю компактные сравнения участников и общий итог чата. '
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
    parser.add_argument('--participants', type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.output, args.send_to_owner, args.participants))
