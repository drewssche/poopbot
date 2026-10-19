"""Existing question buttons shared by the bot and Mini App notification worker."""

BRISTOL_CHOICES: list[tuple[str, str]] = [
    ("12", "\U0001F9F1 1\u20132 (\u0436\u0451\u0441\u0442\u043a\u043e / \u0441\u0443\u0445\u043e)"),
    ("34", "\U0001F34C 3\u20134 (\u043d\u043e\u0440\u043c\u0430)"),
    ("56", "\U0001F366 5\u20136 (\u043c\u044f\u0433\u043a\u043e)"),
    ("7", "\U0001F4A6 7 (\u0432\u043e\u0434\u0438\u0447\u043a\u0430)"),
]

FEELING_CHOICES: list[tuple[str, str]] = [
    ("great", "\U0001F607 \u041f\u0440\u0435\u043a\u0440\u0430\u0441\u043d\u043e"),
    ("ok", "\U0001F610 \u0421\u043e\u0439\u0434\u0451\u0442"),
    ("bad", "\U0001F62B \u0423\u0436\u0430\u0441\u043d\u043e"),
]


def q2_markup(selected_choice=None, *, private_flow=False, event_n=None):
    target_n = int(event_n or 1)
    rows = [[{'text': text, 'callback_data': f'q2:set:{target_n}:{choice}' if private_flow else f'q2:{choice}'}]
            for choice, text in BRISTOL_CHOICES]
    if private_flow:
        rows.append([{'text': '⬅️ Назад', 'callback_data': 'q2:back_q1'},
                     {'text': 'Пропустить', 'callback_data': f'q2:skip:{target_n}'}])
    return {'inline_keyboard': rows}


def q3_markup(selected_choice=None, *, private_flow=False, event_n=None):
    target_n = int(event_n or 1)
    rows = [[{'text': text, 'callback_data': f'q3:set:{target_n}:{choice}' if private_flow else f'q3:{choice}'}]
            for choice, text in FEELING_CHOICES]
    if private_flow:
        rows.append([{'text': '⬅️ Вернуться', 'callback_data': f'q3:back_q2:{target_n}'},
                     {'text': 'Пропустить', 'callback_data': f'q3:skip:{target_n}'}])
    return {'inline_keyboard': rows}
