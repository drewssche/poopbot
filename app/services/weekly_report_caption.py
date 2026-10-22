"""A complete short summary that fits Telegram's 1024-character photo caption."""
from collections import Counter

from app.services.stats_common import Range, previous_period_range, estimate_waste_metrics, format_mass, format_water
from app.services.stats_metrics import chat_origin_events_in_range, bristol_bucket, feeling_emoji, compute_chat_period_metrics
from app.services.stats_rankings import rank_chat_among_groups_by_total
from app.services.weekly_report_card import period_text, plural
from app.services.report_periods import report_period


def build_caption(db, chat_id, card):
    spec = report_period(card.period)
    title = ' '.join(card.chat_title.split())[:90]
    if card.layout == 'weekly':
        # The complete report is on the image; do not repeat it below.
        return '\n'.join(filter(None, [(card.label or '')[:60], title, 'Подробности: /stats · /app']))
    lines = []
    if card.label:
        lines.append(card.label[:60])
    lines.extend([f'📉 {spec.heading} · {period_text(card.start, card.end)}', title, ''])
    lines.append(f'💩 {card.total} {plural(card.total, "отметка", "отметки", "отметок")} · '
                 f'{card.active_days} из {card.period_days} дней')
    if not card.private:
        n = len(card.participants)
        lines.append(f'👥 {n} {plural(n, "участник", "участника", "участников")}')
    prev_range = previous_period_range(card.start, card.period)
    prev = compute_chat_period_metrics(db, chat_id, prev_range, user_id=chat_id if card.private else None)
    delta = card.total-prev.total_poops
    lines.append(f'{spec.comparison}: {delta:+d} отметок (было {prev.total_poops})')
    if not card.private:
        rank, total = rank_chat_among_groups_by_total(db, chat_id, Range(card.start, card.end))
        prev_rank, _ = rank_chat_among_groups_by_total(db, chat_id, prev_range)
        if rank is not None:
            trend = f' · {prev_rank-rank:+d} поз.' if prev_rank is not None else ''
            lines.append(f'Место среди чатов: {rank} из {total}{trend}')
    if card.total:
        rows = chat_origin_events_in_range(db, chat_id, Range(card.start, card.end),
                                          user_id=chat_id if card.private else None)
        br = Counter(bristol_bucket(row[3]) for row in rows)
        fe = Counter(feeling_emoji(row[4]) for row in rows)
        def distribution(counts, symbols):
            values = [f'{symbol} {counts[symbol]}' for symbol in symbols if counts[symbol]]
            if counts[None]:
                values.append(f'не указано: {counts[None]}')
            return ' · '.join(values)
        lines.extend(['', 'Консистенция: '+distribution(br, ('🧱', '🍌', '🍦', '💦')),
                      'Самочувствие: '+distribution(fe, ('😇', '😐', '😫'))])
        mass, water, gallons = estimate_waste_metrics(card.total)
        lines.extend(['', f'Оценка: {format_mass(mass)} · {format_water(water, gallons)} воды на смыв'])
    else:
        lines.extend(['', spec.empty + ' пока нет отметок.'])
    lines.extend(['', 'Полная статистика: /stats · приложение: /app'])
    caption = '\n'.join(lines)
    # No HTML entities or chopped links; Telegram receives plain text.
    if len(caption.encode('utf-16-le'))//2 > 1024:
        raise ValueError('Report caption exceeds Telegram limit')
    return caption
