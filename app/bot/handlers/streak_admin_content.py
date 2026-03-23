from __future__ import annotations

from datetime import date


def _candidate_block(candidates: list[dict[str, int | str]] | None) -> str:
    if not candidates:
        return "Подозрительные даты: пока не найдены.\n"
    lines = ["Подозрительные даты инцидента:"]
    for item in candidates:
        lines.append(
            f"• `{item['date']}` — всего {item['total']}, группы {item['groups']}, лички {item['private']}"
        )
    return "\n".join(lines) + "\n"


def streak_admin_text(target_date: date, *, candidates: list[dict[str, int | str]] | None = None) -> str:
    return (
        "🛠 Управление восстановлением стрика\n\n"
        f"Текущая дата инцидента: `{target_date.isoformat()}`\n\n"
        f"{_candidate_block(candidates)}\n"
        "Что делает панель:\n"
        "• рассылает сервисное сообщение с кнопкой восстановления;\n"
        "• отдельно по всем группам или по всем личкам;\n"
        "• умеет отправить безопасное превью только тебе;\n"
        "• умеет отправить боевое тестовое сообщение только тебе;\n"
        "• умеет отменить тестовое восстановление только тебе;\n"
        "• не дублирует отправку в уже обработанные чаты.\n\n"
        "Рекомендация:\n"
        "• сначала возьми `топ-кандидат`, если он выглядит правдоподобно;\n"
        "• кнопки `Дата -1 день` и `Дата +1 день` нужны для ручной корректировки даты;\n"
        "• сначала проверь `Превью в личку`, затем `Боевое себе в личку`;\n"
        "• для массового инцидента сначала отправь в группы;\n"
        "• затем, если нужно, отдельно в лички."
    )


def streak_admin_result_text(scope_label: str, target_date: date, *, sent: int, skipped: int, failed: int) -> str:
    return (
        "✅ Рассылка завершена\n\n"
        f"Дата инцидента: `{target_date.isoformat()}`\n"
        f"Куда: {scope_label}\n\n"
        f"Отправлено: {sent}\n"
        f"Пропущено как дубликат/неподходящее: {skipped}\n"
        f"Ошибок: {failed}"
    )


def streak_admin_group_picker_text(target_date: date, *, page: int, total_groups: int) -> str:
    return (
        "🛠 Выбор группы для точечной рассылки\n\n"
        f"Дата инцидента: `{target_date.isoformat()}`\n"
        f"Активных групп: {total_groups}\n"
        f"Страница: {page + 1}\n\n"
        "Нажми на нужную группу, и бот отправит туда одно сервисное сообщение "
        "с кнопкой восстановления. Повторная отправка в ту же группу не задублируется."
    )
