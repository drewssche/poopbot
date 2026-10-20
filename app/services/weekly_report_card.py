"""Deterministic, CPU-only rendering of the approved PoopCheck weekly card."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from app.db.models import User
from app.services.stats_common import Range
from app.services.stats_metrics import chat_origin_events_in_range

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'reports'
INK, BROWN, MUTED, BEIGE, PALE = '#181d27', '#934c21', '#645b5b', '#c8b7a7', '#f4f0ec'
MONTHS = ('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split()


@dataclass(frozen=True)
class Participant:
    name: str
    count: int


@dataclass(frozen=True)
class WeeklyCard:
    start: date
    end: date
    chat_title: str
    days: tuple[int, ...]
    participants: tuple[Participant, ...]
    private: bool = False
    label: str | None = None

    @property
    def total(self):
        return sum(self.days)

    @property
    def active_days(self):
        return sum(n > 0 for n in self.days)


def project_card(db, chat_id, start, end, title, label=None):
    if end - start != timedelta(days=6):
        raise ValueError('Weekly report must cover exactly seven days')
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
    return WeeklyCard(start, end, title, tuple(by_day[start + timedelta(days=n)] for n in range(7)),
                      tuple(participants), chat_id > 0, label)


def plural(n, one, few, many):
    return many if 11 <= n % 100 <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def period_text(start, end):
    if start.year != end.year:
        return f'{start.day} {MONTHS[start.month-1]} {start.year} – {end.day} {MONTHS[end.month-1]} {end.year}'
    if start.month != end.month:
        return f'{start.day} {MONTHS[start.month-1]} – {end.day} {MONTHS[end.month-1]} {end.year}'
    return f'{start.day}–{end.day} {MONTHS[end.month-1]} {end.year}'


def render_card(card: WeeklyCard) -> bytes:
    # Keep the approved typography, spacing, assets and round shapes at 840 px.
    shown = card.participants[:8]
    rows = max(1, len(shown))
    extra = len(card.participants) - len(shown)
    footer_y = 780 + rows * 61 + (40 if extra else 0)
    image = Image.new('RGB', (840, footer_y + 76), 'white')
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
    text(48, 133, 'Итоги недели', 46, weight=650)
    date_label = period_text(card.start, card.end)
    size = 29 if len(date_label) < 34 else 25
    text(48, 199, date_label, size, BROWN, 500, width=744)
    text(48, 248, card.chat_title, 23, MUTED, width=744)
    poop = Image.open(ASSETS / 'poop-day.png').convert('RGBA').resize((78, 75), Image.Resampling.LANCZOS)
    for n, count in enumerate(card.days):
        cx = 48 + (744 / 7) * (n + .5)
        text(cx, 305, ('ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС')[n], 23, BEIGE, 650, anchor='mt')
        day = str((card.start + timedelta(days=n)).day)
        if count:
            image.paste(poop, (round(cx - 39), 349), poop)
            text(cx, 376, day, 30, 'white', 500, anchor='mt')
            if count > 1:
                # Always circular: shrink digits, never stretch the badge.
                draw.ellipse((cx + 10, 347, cx + 41, 378), fill='#ffdb00')
                label = str(count) if count < 1000 else '999+'
                text(cx + 25.5, 353 if len(label) < 3 else 356, label,
                     21 if len(label) == 1 else 17 if len(label) == 2 else 12, BROWN, 650, anchor='mt')
        else:
            draw.ellipse((cx - 34, 357, cx + 34, 425), fill=PALE)
            text(cx, 376, day, 30, BEIGE, 500, anchor='mt')
    draw.ellipse((48, 459, 63, 474), fill=PALE, outline='#e8e0d8')
    text(73, 455, 'Нет отметок · цифра в жёлтом кружке — количество', 20, MUTED)
    for x in (48, 430):
        draw.rounded_rectangle((x, 506, x + 362, 649), radius=26, fill=PALE)
    text(76, 528, str(card.total), 56, BROWN, 650, width=306)
    text(76, 603, plural(card.total, 'отметка', 'отметки', 'отметок') + ' за неделю', 23)
    if card.private:
        text(458, 528, str(card.active_days), 56, BROWN, 650)
        text(458, 603, plural(card.active_days, 'день', 'дня', 'дней') + ' с отметками', 23)
    else:
        count = len(card.participants)
        text(458, 528, str(count), 56, BROWN, 650, width=306)
        text(458, 603, plural(count, 'участник отметился', 'участника отметились', 'участников отметились'), 23)
    text(48, 675, f'{card.active_days} из 7 дней с отметками', 23, MUTED)
    text(48, 733, 'Твои отметки' if card.private else 'Участники', 29, weight=650)
    if not shown:
        text(48, 798, 'За эту неделю пока нет отметок', 23, MUTED)
    for n, participant in enumerate(shown):
        y = 784 + n * 61
        draw.ellipse((48, y, 92, y+44), fill=PALE)
        text(70, y+11, clean(participant.name)[:1] or '?', 21, BROWN, 500, anchor='mt')
        text(108, y+9, clean(participant.name) or 'Участник', 25, width=425)
        count_label = str(participant.count)
        unit = plural(participant.count, 'отметка', 'отметки', 'отметок')
        unit_w = draw.textlength(unit, font=font(20))
        text(792, y+12, unit, 20, MUTED, anchor='rt')
        text(780-unit_w, y+9, count_label, 25, BROWN, 600, anchor='rt')
        if n < len(shown)-1:
            draw.line((48, y+52, 792, y+52), fill='#eee8e3')
    if extra:
        text(48, 790+rows*61, f'И ещё {extra} {plural(extra, "участник", "участника", "участников")} · полный отчёт ниже', 20, MUTED)
    draw.line((48, footer_y, 792, footer_y), fill='#eee8e3')
    text(48, footer_y+22, 'Личная история' if card.private else 'Отметки этого чата', 18, MUTED)
    text(792, footer_y+22, '@poopcheck_bot', 18, MUTED, anchor='rt')
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
