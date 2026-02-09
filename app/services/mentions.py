from aiogram.utils.markdown import hlink


def mention_user(user_id: int, display_name: str, username: str | None) -> str:
    # Если есть username — упоминаем через @
    if username:
        return f"@{username}"
    # Иначе делаем кликабельное имя
    return hlink(display_name, f"tg://user?id={user_id}")
