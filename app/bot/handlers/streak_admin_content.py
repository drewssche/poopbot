from __future__ import annotations

from datetime import date
from app.services.restore_period_service import RestorePeriod


def streak_admin_text(target_date: date, *, candidates=None, restore_enabled: bool = False, period: RestorePeriod | None = None) -> str:
    label = period.label if period else "не выбран"
    return (
        "🛠 Управление восстановлением стрика\n\n"
        f"Период: {label}\n"
        f"Статус восстановления: {'ВКЛ' if restore_enabled else 'ВЫКЛ'}\n\n"
        "Период сохраняется до твоего изменения; даты сами не сдвигаются. "
        "Новый выбор действует и для ранее отправленных сервисных кнопок.\n\n"
        "Кнопка добавляет по одной отметке за каждый пропущенный день только нажавшему участнику и только в этом чате.\n\n"
        "Сначала проверь превью или боевое сообщение себе, затем выбери адресатов. "
        "Отмена убирает только твои неизменённые тестовые отметки выбранного периода."
    )


def streak_admin_result_text(scope_label: str, target_date: date, *, sent: int, skipped: int, failed: int, period: RestorePeriod | None = None) -> str:
    return (
        "✅ Рассылка завершена\n\n"
        f"Период: {period.label if period else 'выбранный в /streak'}\n"
        f"Куда: {scope_label}\n\n"
        f"Отправлено: {sent}\nПропущено: {skipped}\nОшибок: {failed}"
    )


def streak_admin_group_picker_text(target_date: date, *, page: int, total_groups: int, period: RestorePeriod | None = None) -> str:
    return (
        "🛠 Выбор группы\n\n"
        f"Период: {period.label if period else 'выбранный в /streak'}\n"
        f"Активных групп: {total_groups}\nСтраница: {page + 1}\n\n"
        "Нажми на группу, чтобы отправить сервисное сообщение. Повторное нажатие отправит его ещё раз."
    )
