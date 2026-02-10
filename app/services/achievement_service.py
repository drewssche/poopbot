from __future__ import annotations

import random


ACHIEVEMENTS = {
    (1, 1): ["Стартанул", "Разминка", "Первый пошёл"],
    (2, 3): ["Стабильный", "По графику", "Режимный"],
    (4, 5): ["Говнопушка!", "Турборежим", "Двигатель прогрет"],
    (6, 7): ["Штормит", "Конвейер", "Многоходовочка"],
    (8, 10): ["Легенда", "Портал открыт", "Гига-режим"],
}


def pick_achievement(n: int) -> str:
    for (a, b), options in ACHIEVEMENTS.items():
        if a <= n <= b:
            return random.choice(options)
    return "Стартанул"
