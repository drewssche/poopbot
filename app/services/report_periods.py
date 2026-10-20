"""Shared labels and schedule keys for calendar report periods."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReportPeriod:
    heading: str
    duration: str
    comparison: str
    empty: str
    command: str


PERIODS = {
    'week': ReportPeriod('Итоги недели', 'за неделю', 'К прошлой неделе', 'За эту неделю', 'weekly_stats'),
    'month': ReportPeriod('Итоги месяца', 'за месяц', 'К прошлому месяцу', 'За этот месяц', 'monthly_stats'),
    'year': ReportPeriod('Итоги года', 'за год', 'К прошлому году', 'За этот год', 'yearly_stats'),
}


def report_period(kind):
    try:
        return PERIODS[kind]
    except KeyError:
        raise ValueError(f'Unsupported report period: {kind}') from None
