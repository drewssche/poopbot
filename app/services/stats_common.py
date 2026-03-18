from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Range:
    start: date
    end: date


def period_to_range(today: date, period: str) -> Range:
    if period == "today":
        return Range(today, today)
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return Range(start, start + timedelta(days=6))
    if period == "month":
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return Range(start, end)
    if period == "year":
        return Range(date(today.year, 1, 1), date(today.year, 12, 31))
    return Range(date(1970, 1, 1), today)


def previous_period_range(today: date, period: str) -> Range:
    curr = period_to_range(today, period)
    if period == "week":
        end = curr.start - timedelta(days=1)
        return Range(end - timedelta(days=6), end)
    if period == "month":
        prev_month_last = curr.start - timedelta(days=1)
        start = prev_month_last.replace(day=1)
        end = prev_month_last.replace(day=calendar.monthrange(prev_month_last.year, prev_month_last.month)[1])
        return Range(start, end)
    if period == "year":
        y = today.year - 1
        return Range(date(y, 1, 1), date(y, 12, 31))
    if period == "today":
        prev = today - timedelta(days=1)
        return Range(prev, prev)
    prev = curr.start - timedelta(days=1)
    return Range(date(1970, 1, 1), prev)


def period_label(period: str) -> str:
    if period == "week":
        return "за неделю"
    if period == "month":
        return "за месяц"
    if period == "year":
        return "за год"
    if period == "today":
        return "за день"
    return "за всё время"


GRAMS_PER_POOP_ESTIMATE = 150.0
LITERS_PER_FLUSH_ESTIMATE = 6.0
GALLONS_PER_LITER = 0.264172


def estimate_waste_metrics(poops_n: int) -> tuple[float, float, float]:
    n = max(0, int(poops_n))
    mass_g = float(n) * GRAMS_PER_POOP_ESTIMATE
    water_l = float(n) * LITERS_PER_FLUSH_ESTIMATE
    water_gal = water_l * GALLONS_PER_LITER
    return mass_g, water_l, water_gal


def format_mass(mass_g: float) -> str:
    if mass_g >= 1_000_000.0:
        return f"{mass_g / 1_000_000.0:.2f} т"
    if mass_g >= 1_000.0:
        return f"{mass_g / 1_000.0:.2f} кг"
    return f"{int(round(mass_g))} г"


def format_water(water_l: float, water_gal: float) -> str:
    return f"{water_l:.0f} л ({water_gal:.1f} гал)"


def format_period(r: Range) -> str:
    return f"{r.start.strftime('%d.%m.%y')}–{r.end.strftime('%d.%m.%y')}"
