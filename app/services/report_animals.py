"""Playful mass equivalents, using explicit rounded reference weights."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite, log


@dataclass(frozen=True)
class Animal:
    icon: str
    name: str
    forms: tuple[str, str, str]
    kg: float


# Illustrative references, not species-wide averages. Sources in docs/report-animals.md.
ANIMALS = (
    Animal('dove', 'Голубь', ('голубь', 'голубя', 'голубей'), .35),
    Animal('cat', 'Кошка', ('кошка', 'кошки', 'кошек'), 4.5),
    Animal('dog', 'Лабрадор', ('лабрадор', 'лабрадора', 'лабрадоров'), 30),
    Animal('horse', 'Лошадь', ('лошадь', 'лошади', 'лошадей'), 450),
    Animal('hippo', 'Бегемот', ('бегемот', 'бегемота', 'бегемотов'), 1500),
)


def mass_comparison(kg):
    if not isfinite(kg) or kg <= 0:
        return None
    animal = min(ANIMALS, key=lambda item: abs(log(kg / item.kg)))
    amount = (Decimal(str(kg)) / Decimal(str(animal.kg))).quantize(
        Decimal('.1'), rounding=ROUND_HALF_UP)
    if amount < Decimal('.1'):
        return animal, f'Меньше 0,1 {animal.forms[1]}'
    if amount == int(amount):
        count = int(amount)
        index = 2 if 11 <= count % 100 <= 14 else 0 if count % 10 == 1 else 1 if 2 <= count % 10 <= 4 else 2
        label = f'{count:,}'.replace(',', ' ')
    else:
        index = 1
        label = str(amount).replace('.', ',')
    return animal, f'≈ {label} {animal.forms[index]}'
