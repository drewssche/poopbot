import pytest
from PIL import Image

from app.services.report_animals import ANIMALS, mass_comparison
from app.services.report_layouts import Canvas, draw_estimates
from app.services.weekly_report_card import ASSETS


@pytest.mark.parametrize('kg,icon,label', [
    (.15, 'dove', '≈ 0,4 голубя'),
    (3.15, 'cat', '≈ 0,7 кошки'),
    (4.5, 'cat', '≈ 1 кошка'),
    (30, 'dog', '≈ 1 лабрадор'),
    (450, 'horse', '≈ 1 лошадь'),
    (1500, 'hippo', '≈ 1 бегемот'),
    (33000, 'hippo', '≈ 22 бегемота'),
    (16500, 'hippo', '≈ 11 бегемотов'),
])
def test_reference_selection_rounding_and_russian_labels(kg, icon, label):
    animal, text = mass_comparison(kg)
    assert animal.icon == icon and text == label


@pytest.mark.parametrize('kg', [0, -1, float('inf'), float('nan')])
def test_no_animal_for_empty_or_invalid_mass(kg):
    assert mass_comparison(kg) is None


def test_estimates_convert_grams_and_use_blue_for_water(monkeypatch):
    c = Canvas(340)
    calls = []
    original = c.text
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        original(*args, **kwargs)
    monkeypatch.setattr(c, 'text', capture)
    draw_estimates(c, 0, 21, 'Примерно')
    values = [args[2] for args, _ in calls]
    assert '3,15 кг' in values and '126 л' in values
    assert '≈ 0,7 кошки' in values
    assert 'По массе · кошка ≈ 4,50 кг' in values
    water = next(args for args, _ in calls if args[2] == '126 л')
    assert water[4] == '#3DA5FF'
    assert c.im.getpixel((489, 100)) == (61, 165, 255)


def test_all_silhouettes_are_local_nonempty_transparent_assets():
    for animal in ANIMALS:
        icon = Image.open(ASSETS / f'animal-{animal.icon}.png')
        assert icon.mode == 'RGBA' and icon.getbbox()
