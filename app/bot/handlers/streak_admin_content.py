from __future__ import annotations

from datetime import date


def streak_admin_text(target_date: date) -> str:
    return (
        "🛠 Управление восстановлением стрика\n\n"
        f"Текущая дата инцидента: `{target_date.isoformat()}`\n\n"
        "Что делает панель:\n"
        "• рассылает сервисное сообщение с кнопкой восстановления;\n"
        "• отдельно по всем группам или по всем личкам;\n"
        "• не дублирует отправку в уже обработанные чаты.\n\n"
        "Рекомендация:\n"
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
