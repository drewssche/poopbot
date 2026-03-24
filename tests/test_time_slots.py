"""Тесты для функции временных слотов."""
import unittest
from datetime import datetime, timezone

from app.services.time_service import (
    get_time_slot,
    get_slot_emoji,
    get_slot_title,
    get_dominant_slot,
    get_slot_popup,
)


class TimeSlotTests(unittest.TestCase):
    """Тесты функций временных слотов."""

    def test_get_time_slot_night(self) -> None:
        """Ночь: 00:00-06:00."""
        for hour in [0, 1, 2, 3, 4, 5]:
            dt = datetime(2026, 3, 24, hour, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(get_time_slot(dt, "UTC"), "night")

    def test_get_time_slot_morning(self) -> None:
        """Утро: 06:00-12:00."""
        for hour in [6, 7, 8, 9, 10, 11]:
            dt = datetime(2026, 3, 24, hour, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(get_time_slot(dt, "UTC"), "morning")

    def test_get_time_slot_afternoon(self) -> None:
        """День: 12:00-18:00."""
        for hour in [12, 13, 14, 15, 16, 17]:
            dt = datetime(2026, 3, 24, hour, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(get_time_slot(dt, "UTC"), "afternoon")

    def test_get_time_slot_evening(self) -> None:
        """Вечер: 18:00-24:00."""
        for hour in [18, 19, 20, 21, 22, 23]:
            dt = datetime(2026, 3, 24, hour, 0, 0, tzinfo=timezone.utc)
            self.assertEqual(get_time_slot(dt, "UTC"), "evening")

    def test_get_slot_emoji(self) -> None:
        """Эмодзи слотов."""
        self.assertEqual(get_slot_emoji("night"), "🌙")
        self.assertEqual(get_slot_emoji("morning"), "🌅")
        self.assertEqual(get_slot_emoji("afternoon"), "☀️")
        self.assertEqual(get_slot_emoji("evening"), "🌆")
        self.assertEqual(get_slot_emoji("unknown"), "⏰")

    def test_get_slot_title(self) -> None:
        """Титулы слотов."""
        self.assertEqual(get_slot_title("night"), "Ночной серун")
        self.assertEqual(get_slot_title("morning"), "Утренний жаворонок")
        self.assertEqual(get_slot_title("afternoon"), "Дневной трудяга")
        self.assertEqual(get_slot_title("evening"), "Вечерний философ")
        self.assertEqual(get_slot_title("unknown"), "Участник")

    def test_get_dominant_slot_clear_winner(self) -> None:
        """Явный победитель."""
        counts = {"night": 1, "morning": 10, "afternoon": 4, "evening": 3}
        self.assertEqual(get_dominant_slot(counts), "morning")

    def test_get_dominant_slot_all_equal(self) -> None:
        """Все слоты равны — Круглосуточный."""
        counts = {"night": 5, "morning": 5, "afternoon": 5, "evening": 5}
        self.assertEqual(get_dominant_slot(counts), "all_day")

    def test_get_dominant_slot_empty(self) -> None:
        """Пустые данные."""
        counts = {"night": 0, "morning": 0, "afternoon": 0, "evening": 0}
        self.assertIsNone(get_dominant_slot(counts))

    def test_get_slot_popup_night(self) -> None:
        """Ночной попап."""
        self.assertEqual(get_slot_popup("night", 3), "Ночной серун 🌙")

    def test_get_slot_popup_morning_early(self) -> None:
        """Раннее утро."""
        self.assertEqual(get_slot_popup("morning", 7), "Кофе с сигаркой ☕")

    def test_get_slot_popup_morning_late(self) -> None:
        """Позднее утро."""
        self.assertEqual(get_slot_popup("morning", 10), "Доброе утро 🌅")

    def test_get_slot_popup_afternoon_early(self) -> None:
        """Ранний день."""
        self.assertEqual(get_slot_popup("afternoon", 13), "После обеда 🍽️")

    def test_get_slot_popup_afternoon_late(self) -> None:
        """Поздний день."""
        self.assertEqual(get_slot_popup("afternoon", 16), "Дневной сеанс ☀️")

    def test_get_slot_popup_evening_early(self) -> None:
        """Ранний вечер."""
        self.assertEqual(get_slot_popup("evening", 19), "Вечерний ритуал 🌆")

    def test_get_slot_popup_evening_late(self) -> None:
        """Поздний вечер."""
        self.assertEqual(get_slot_popup("evening", 22), "Почти ночь 🌙")


if __name__ == "__main__":
    unittest.main()
