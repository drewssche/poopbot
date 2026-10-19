"""Q1 markup shared by the bot and the small Mini App API image."""

def q1_markup(has_any_members, show_q2_q3_button=False, show_restore_streak_button=False):
    plus = {'text': '+💩', 'callback_data': 'q1:plus'}
    rows = [[{'text': '-💩', 'callback_data': 'q1:minus'}, plus]] if has_any_members else [[plus]]
    if show_q2_q3_button:
        rows.append([{'text': '🧻 Уточняющие вопросы', 'callback_data': 'q1:q2q3'}])
    if show_restore_streak_button:
        rows.append([{'text': '♻️ Вернуть стрик', 'callback_data': 'q1:restore_streak'}])
    return {'inline_keyboard': rows}
