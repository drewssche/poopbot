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
    fractional: str | None = None


# Illustrative references, not species-wide averages. Sources in docs/report-animals.md.
ANIMALS = (
    Animal('mouse', 'Мышь', ('мышь', 'мыши', 'мышей'), 0.025),
    Animal('gerbil', 'Песчанка', ('песчанка', 'песчанки', 'песчанок'), 0.1),
    Animal('dove', 'Голубь', ('голубь', 'голубя', 'голубей'), 0.35),
    Animal('squirrel', 'Белка', ('белка', 'белки', 'белок'), 0.6),
    Animal('guinea-pig', 'Морская свинка', ('морская свинка', 'морские свинки', 'морских свинок'), 0.9, 'морской свинки'),
    Animal('hedgehog', 'Ёж', ('ёж', 'ежа', 'ежей'), 1.0),
    Animal('duck', 'Утка', ('утка', 'утки', 'уток'), 1.1),
    Animal('rabbit', 'Кролик', ('кролик', 'кролика', 'кроликов'), 1.8),
    Animal('chicken', 'Петух', ('петух', 'петуха', 'петухов'), 2.5),
    Animal('goose', 'Гусь', ('гусь', 'гуся', 'гусей'), 3.5),
    Animal('cat', 'Кошка', ('кошка', 'кошки', 'кошек'), 4.5),
    Animal('fox', 'Лиса', ('лиса', 'лисы', 'лис'), 6),
    Animal('raccoon', 'Енот', ('енот', 'енота', 'енотов'), 8),
    Animal('badger', 'Барсук', ('барсук', 'барсука', 'барсуков'), 12),
    Animal('beaver', 'Бобр', ('бобр', 'бобра', 'бобров'), 20),
    Animal('dog', 'Лабрадор', ('лабрадор', 'лабрадора', 'лабрадоров'), 30),
    Animal('capybara', 'Капибара', ('капибара', 'капибары', 'капибар'), 50),
    Animal('sheep', 'Овца', ('овца', 'овцы', 'овец'), 70),
    Animal('panda', 'Панда', ('панда', 'панды', 'панд'), 100),
    Animal('gorilla', 'Горилла', ('горилла', 'гориллы', 'горилл'), 160),
    Animal('lion', 'Лев', ('лев', 'льва', 'львов'), 190),
    Animal('pig', 'Кабан', ('кабан', 'кабана', 'кабанов'), 250),
    Animal('bear', 'Бурый медведь', ('бурый медведь', 'бурых медведя', 'бурых медведей'), 350, 'бурого медведя'),
    Animal('horse', 'Лошадь', ('лошадь', 'лошади', 'лошадей'), 450),
    Animal('camel', 'Верблюд', ('верблюд', 'верблюда', 'верблюдов'), 600),
    Animal('bison', 'Бизон', ('бизон', 'бизона', 'бизонов'), 800),
    Animal('giraffe', 'Жираф', ('жираф', 'жирафа', 'жирафов'), 1200),
    Animal('hippo', 'Бегемот', ('бегемот', 'бегемота', 'бегемотов'), 1500),
    Animal('rhino', 'Белый носорог', ('белый носорог', 'белых носорога', 'белых носорогов'), 2300, 'белого носорога'),
    Animal('elephant', 'Африканский слон', ('африканский слон', 'африканских слона', 'африканских слонов'), 5000, 'африканского слона'),
)


def mass_comparison(kg):
    if not isfinite(kg) or kg <= 0:
        return None
    animal = min(ANIMALS, key=lambda item: abs(log(kg / item.kg)))
    amount = (Decimal(str(kg)) / Decimal(str(animal.kg))).quantize(
        Decimal('.1'), rounding=ROUND_HALF_UP)
    if amount < Decimal('.1'):
        return animal, f'Меньше 0,1 {animal.fractional or animal.forms[1]}'
    if amount == int(amount):
        count = int(amount)
        index = 2 if 11 <= count % 100 <= 14 else 0 if count % 10 == 1 else 1 if 2 <= count % 10 <= 4 else 2
        label = f'{count:,}'.replace(',', ' ')
    else:
        label = str(amount).replace('.', ',')
        return animal, f'≈ {label} {animal.fractional or animal.forms[1]}'
    return animal, f'≈ {label} {animal.forms[index]}'
