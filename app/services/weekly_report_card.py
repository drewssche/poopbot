"""Deterministic, CPU-only rendering of the approved PoopCheck calendar cards."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from app.db.models import User
from app.services.stats_common import Range, period_to_range
from app.services.report_periods import report_period
from app.services.stats_metrics import chat_origin_events_in_range

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'reports'
INK, BROWN, MUTED, BEIGE, PALE = '#181d27', '#934c21', '#645b5b', '#c8b7a7', '#f4f0ec'
MONTHS = ('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split()


@dataclass(frozen=True)
class Participant:
    name: str
    count: int


@dataclass(frozen=True)
class ReportCard:
    start: date
    end: date
    chat_title: str
    days: tuple[int, ...]
    participants: tuple[Participant, ...]
    private: bool = False
    label: str | None = None
    period: str = 'week'

    @property
    def total(self):
        return sum(self.days)

    @property
    def active_days(self):
        return sum(n > 0 for n in self.days)


    @property
    def period_days(self):
        return (self.end - self.start).days + 1

    @property
    def months(self):
        counts = [0] * 12
        for n, count in enumerate(self.days):
            counts[(self.start + timedelta(days=n)).month - 1] += count
        return tuple(counts)


# Compatibility for scripts which constructed weekly sample cards directly.
WeeklyCard = ReportCard


def project_card(db, chat_id, start, end, title, label=None, *, period='week'):
    report_period(period)
    expected = period_to_range(start, period)
    if (start, end) != (expected.start, expected.end):
        raise ValueError('Report must cover a complete calendar period')
    rows = chat_origin_events_in_range(db, chat_id, Range(start, end),
                                      user_id=chat_id if chat_id > 0 else None)
    by_day = Counter(row[0] for row in rows)
    by_user = Counter(int(row[1]) for row in rows)
    users = {u.user_id: u for u in db.scalars(select(User).where(User.user_id.in_(by_user))).all()} if by_user else {}
    participants = []
    for uid, count in sorted(by_user.items(), key=lambda item: (-item[1], item[0])):
        user = users.get(uid)
        name = (user.first_name or user.username) if user else None
        participants.append(Participant(name or 'Участник', count))
    return ReportCard(start, end, title, tuple(by_day[start + timedelta(days=n)] for n in range((end-start).days+1)),
                      tuple(participants), chat_id > 0, label, period)


def plural(n, one, few, many):
    return many if 11 <= n % 100 <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def period_text(start, end):
    if start.year != end.year:
        return f'{start.day} {MONTHS[start.month-1]} {start.year} – {end.day} {MONTHS[end.month-1]} {end.year}'
    if start.month != end.month:
        return f'{start.day} {MONTHS[start.month-1]} – {end.day} {MONTHS[end.month-1]} {end.year}'
    return f'{start.day}–{end.day} {MONTHS[end.month-1]} {end.year}'


def render_card(card: ReportCard) -> bytes:
    # Keep the approved typography, spacing, assets and round shapes at 840 px.
    spec = report_period(card.period)
    if len(card.days) != card.period_days:
        raise ValueError('One count per day is required')
    if card.period == 'month':
        calendar_rows = (card.start.weekday() + card.period_days + 6) // 7
        shift = (calendar_rows - 1) * 94
    elif card.period == 'year':
        shift = 2 * 136
    else:
        shift = 0
    # Fixed height for each calendar layout, independent of the group size.
    footer_y = 612 + shift
    image = Image.new('RGB', (840, footer_y + 56), 'white')
    draw = ImageDraw.Draw(image)
    fonts = {}

    def font(size, weight=400):
        key = size, weight
        if key not in fonts:
            fonts[key] = ImageFont.truetype(str(ASSETS / f'Inter-{weight}.ttf'), size)
        return fonts[key]

    def clean(value):
        # The original Inter asset has no emoji glyphs. Avoid tofu and control chars
        # in user-provided titles/names, retaining letters from supported scripts.
        return ' '.join(''.join(c for c in str(value) if unicodedata.category(c) not in ('Cc', 'Cf', 'So')).split())

    def text(x, y, value, size=23, color=INK, weight=400, width=None, anchor='lt'):
        value = clean(value)
        f = font(size, weight)
        if width:
            original = value
            while value and draw.textlength(value, font=f) > width:
                original = original[:-1]
                value = original + '…'
        draw.text((x, y), value, font=f, fill=color, anchor=anchor)

    logo = Image.open(ASSETS / 'logo.png').convert('RGBA')
    logo.thumbnail((260, 65), Image.Resampling.LANCZOS)
    image.paste(logo, (48, 36), logo)
    if card.label:
        draw.rounded_rectangle((500, 43, 792, 94), radius=26, fill=PALE)
        text(646, 58, card.label, 20, MUTED, width=260, anchor='mt')
    text(48, 133, spec.heading, 46, weight=650)
    date_label = period_text(card.start, card.end)
    size = 29 if len(date_label) < 34 else 25
    text(48, 199, date_label, size, BROWN, 500, width=744)
    poop = Image.open(ASSETS / 'poop-day.png').convert('RGBA').resize((78, 75), Image.Resampling.LANCZOS)
    def cell(cx, top, value, count):
        if count:
            image.paste(poop, (round(cx - 39), top + 2), poop)
            text(cx, top + 29, str(value), 30, 'white', 500, anchor='mt')
            # Always circular: shrink digits, never stretch the badge.
            draw.ellipse((cx + 10, top, cx + 41, top + 31), fill='#ffdb00')
            label = str(count) if count < 1000 else '999+'
            text(cx + 25.5, top + (6 if len(label) < 3 else 9), label,
                 21 if len(label) == 1 else 17 if len(label) == 2 else 12, BROWN, 650, anchor='mt')
        else:
            draw.ellipse((cx - 34, top + 10, cx + 34, top + 78), fill=PALE)
            text(cx, top + 29, str(value), 30, BEIGE, 500, anchor='mt')

    if card.period == 'year':
        for n, count in enumerate(card.months):
            cx = 48 + (744 / 4) * (n % 4 + .5)
            top = 312 + (n // 4) * 136
            text(cx, top - 42, ('ЯНВ', 'ФЕВ', 'МАР', 'АПР', 'МАЙ', 'ИЮН',
                 'ИЮЛ', 'АВГ', 'СЕН', 'ОКТ', 'НОЯ', 'ДЕК')[n], 23, BEIGE, 650, anchor='mt')
            cell(cx, top, n+1, count)
    else:
        for n, weekday in enumerate(('ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС')):
            text(48+(744/7)*(n+.5), 270, weekday, 23, BEIGE, 650, anchor='mt')
        offset = card.start.weekday() if card.period == 'month' else 0
        for n, count in enumerate(card.days):
            position = n + offset
            cx = 48 + (744 / 7) * (position % 7 + .5)
            cell(cx, 312 + (position // 7) * 94, (card.start+timedelta(days=n)).day, count)
    for x in (48, 430):
        draw.rounded_rectangle((x, 428+shift, x + 362, 571+shift), radius=26, fill=PALE)
    text(76, 450+shift, str(card.total), 56, BROWN, 650, width=306)
    text(76, 525+shift, plural(card.total, 'отметка', 'отметки', 'отметок') + ' ' + spec.duration, 23)
    if card.private:
        text(458, 450+shift, str(card.active_days), 56, BROWN, 650)
        text(458, 525+shift, plural(card.active_days, 'день', 'дня', 'дней') + ' с отметками', 23)
    else:
        count = len(card.participants)
        text(458, 450+shift, str(count), 56, BROWN, 650, width=306)
        text(458, 525+shift, plural(count, 'участник отметился', 'участника отметились', 'участников отметились'), 23)
    draw.line((48, footer_y, 792, footer_y), fill='#eee8e3')
    text(48, footer_y+22, 'Личная история' if card.private else 'Отметки этого чата', 18, MUTED)
    text(792, footer_y+22, '@poopcheck_bot', 18, MUTED, anchor='rt')
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
