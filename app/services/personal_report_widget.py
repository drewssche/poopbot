"""Read-only Telegram widgets for personal calendar reports."""

import base64
import calendar
from collections import Counter
from functools import lru_cache
from html import escape
from io import BytesIO
import zlib

from PIL import Image

from app.services.daily_widget_demo import ASSETS
from app.services.group_daily_widget import encode
from app.services.report_animals import mass_comparison
from app.services.report_layouts import animal_icon
from app.services.report_periods import report_period
from app.services.stats_common import (
    Range,
    previous_period_range,
    estimate_waste_metrics,
    format_mass,
)
from app.services.stats_metrics import (
    chat_origin_events_in_range,
    compute_chat_period_metrics,
    bristol_bucket,
    feeling_emoji,
)
from app.services.weekly_report_card import period_text


@lru_cache(maxsize=32)
def miniature(icon):
    mask = (
        animal_icon(icon, 42, 32)
        .getchannel("A")
        .point(lambda alpha: 1 if alpha > 90 else 0)
    )
    image = Image.frombytes("P", mask.size, mask.tobytes())
    image.putpalette([244, 240, 236, 147, 76, 33])
    output = BytesIO()
    image.save(output, format="PNG", bits=1, optimize=True)
    return base64.b64encode(output.getvalue()).decode()


def state(db, chat_id, card):
    if chat_id <= 0 or not card.private:
        raise ValueError("Personal report requires a private chat")
    if card.details:
        previous_total = card.details.previous_total
        previous_active = card.details.previous_active_days
        kinds, feelings = card.details.bristol, card.details.feeling
    else:
        previous = previous_period_range(card.start, card.period)
        metrics = compute_chat_period_metrics(db, chat_id, previous, user_id=chat_id)
        previous_total, previous_active = metrics.total_poops, metrics.active_days_count
        rows = chat_origin_events_in_range(
            db, chat_id, Range(card.start, card.end), user_id=chat_id
        )
        consistency_counts = Counter(bristol_bucket(row[3]) for row in rows)
        feeling_counts = Counter(feeling_emoji(row[4]) for row in rows)
        kinds = [consistency_counts[key] for key in ("🧱", "🍌", "🍦", "💦", None)]
        feelings = [feeling_counts[key] for key in ("😇", "😐", "😫", None)]
    mass, water, _ = estimate_waste_metrics(card.total)
    comparison = mass_comparison(mass / 1000)
    data = dict(
        total=card.total,
        active=card.active_days,
        length=card.period_days,
        previous=previous_total,
        prevActive=previous_active,
        counts=card.months if card.period == "year" else card.days,
        mass=format_mass(mass).replace(".", ","),
        water=round(water),
        kinds=kinds,
        feelings=feelings,
    )
    if comparison:
        animal, label = comparison
        data.update(animal=label, icon=miniature(animal.icon))
    payload = encode(data)
    if len(payload.encode()) > 1024:
        raise ValueError("Report widget state exceeds Telegram limit")
    return payload


def template(row):
    source = (ASSETS / "personal-report.html").read_text()
    logo = base64.b64encode((ASSETS / "logo-110.png").read_bytes()).decode()
    spec = report_period(row.report_period)
    html = (
        source.replace("__LOGO__", logo)
        .replace("__TITLE__", spec.heading)
        .replace("__LABEL__", escape(row.label or "Личный отчёт"))
        .replace("__PERIOD__", period_text(row.period_start, row.period_end))
        .replace("__COMPARE__", spec.comparison)
        .replace("__KIND__", row.report_period)
        .replace("__OFFSET__", str(row.period_start.weekday()))
        .replace("__FIRST__", str(row.period_start.day))
        .replace(
            "__DAYSINMONTH__",
            str(calendar.monthrange(row.period_start.year, row.period_start.month)[1]),
        )
    )
    if len(html.encode()) > 32768 or len(zlib.compress(html.encode(), 9)) > 4010:
        raise ValueError("Report widget exceeds Telegram limit")
    return html
