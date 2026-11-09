import pytest
from PIL import Image

from app.services.report_animals import ANIMALS, mass_comparison
from app.services.report_layouts import Canvas, draw_estimates
from app.services.weekly_report_card import ASSETS


@pytest.mark.parametrize('kg,icon,label', [
    (.15, 'gerbil', '≈ 1,5 песчанки'),
    (3.15, 'goose', '≈ 0,9 гуся'),
    (4.5, 'cat', '≈ 1 кошка'),
    (30, 'dog', '≈ 1 лабрадор'),
    (450, 'horse', '≈ 1 лошадь'),
    (1500, 'hippo', '≈ 1 бегемот'),
    (110000, 'elephant', '≈ 22 африканских слона'),
    (55000, 'elephant', '≈ 11 африканских слонов'),
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
    assert '≈ 0,9 гуся' in values
    assert 'По массе · гусь ≈ 3,50 кг' in values
    water = next(args for args, _ in calls if args[2] == '126 л')
    assert water[4] == '#3DA5FF'
    assert c.im.getpixel((489, 100)) == (61, 165, 255)


def test_all_silhouettes_are_local_nonempty_transparent_assets():
    for animal in ANIMALS:
        icon = Image.open(ASSETS / f'animal-{animal.icon}.png')
        assert icon.mode == 'RGBA' and icon.getbbox()


@pytest.mark.parametrize('total', [0, 21, 12345, 9999999])
def test_estimate_text_fits_inside_visible_icon_height(total):
    from PIL import ImageChops
    from app.services.weekly_report_card import PALE

    canvas = Canvas(340)
    draw_estimates(canvas, 0, total, 'Примерно')

    def ink_bounds(box):
        area = canvas.im.crop(box)
        contrast = ImageChops.difference(area, Image.new('RGB', area.size, PALE))
        # Ignore only the faint outer antialiasing when comparing visible edges.
        return contrast.convert('L').point(lambda p: 255 if p > 50 else 0).getbbox()

    for x in (48, 430):
        icon = ink_bounds((x+14, 56, x+98, 144))
        text = ink_bounds((x+110, 56, x+350, 144))
        assert icon and text and icon[1] <= text[1] < text[3] <= icon[3]


def test_expanded_catalog_has_thirty_distinct_sourced_animals():
    import json
    from hashlib import sha256
    sources = json.loads((ASSETS / 'animal-sources/sources.json').read_text())
    assert len(ANIMALS) == len({a.icon for a in ANIMALS}) == 30
    assert len({sha256((ASSETS / f'animal-{a.icon}.png').read_bytes()).digest() for a in ANIMALS}) == 30
    for animal in ANIMALS:
        chosen, label = mass_comparison(animal.kg)
        assert chosen == animal and label == f'≈ 1 {animal.forms[0]}'
        assert '/publicdomain/' in sources[animal.icon]['license']
        assert (ASSETS / 'animal-sources' / f'{animal.icon}.svg').is_file()


@pytest.mark.parametrize('kg,label', [(7500, '≈ 1,5 африканского слона'),
                                     (.75, '≈ 0,8 морской свинки')])
def test_fractional_comparisons_decline_adjectives(kg, label):
    assert mass_comparison(kg)[1] == label


def participant_card(period='week', count=20, private=False):
    from datetime import date
    from app.services.stats_common import period_to_range
    from app.services.weekly_report_card import Participant, ReportCard, WeeklyDetails
    span = period_to_range(date(2026, 10, 19), period)
    people = tuple(Participant(f'Участник {i+1}', i+1) for i in range(count))
    total = sum(p.count for p in people)
    days = (total,) + (0,) * ((span.end-span.start).days)
    return ReportCard(span.start, span.end, 'Чат', days, people, private=private, period=period,
                      layout='weekly' if period == 'week' else 'summary',
                      details=WeeklyDetails(0, 0, (0,0,0,0,total), (0,0,0,total)))


@pytest.mark.parametrize('period', ['week', 'month', 'year'])
def test_every_group_participant_gets_own_mass_without_large_animal_cards(monkeypatch, period):
    from io import BytesIO
    from app.services.weekly_report_card import render_card
    card = participant_card(period)
    calls = []
    original = Canvas.text
    def capture(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Canvas, 'text', capture)
    picture = render_card(card)
    im = Image.open(BytesIO(picture))
    values = [args[2] for args, _ in calls]
    assert all(p.name in values for p in card.participants)
    # Each row uses that participant's own events, not the group's 210 events.
    assert '150 г · ≈ 1,5 песчанки' in values
    assert '3,00 кг · ≈ 0,9 гуся' in values
    assert len([v for v in values if ' · ≈ ' in str(v)]) == 20
    assert im.width == 840 and im.height < 2300
    assert len(picture) < 1024*1024


@pytest.mark.parametrize('period', ['week', 'month', 'year'])
def test_private_reports_do_not_add_participant_animal_block(monkeypatch, period):
    from app.services.weekly_report_card import render_card
    calls = []
    original = Canvas.text
    def capture(self, *args, **kwargs):
        calls.append(args[2])
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Canvas, 'text', capture)
    render_card(participant_card(period, 1, private=True))
    assert 'Участники · примерная масса' not in calls
    assert not any(' · ≈ ' in str(v) for v in calls)
    assert any(str(v).startswith('По массе ·') for v in calls)


def test_oversized_group_stays_within_telegram_photo_limits_and_discloses_cutoff(monkeypatch):
    from io import BytesIO
    from app.services.weekly_report_card import render_card
    calls = []
    original = Canvas.text
    def capture(self, *args, **kwargs):
        calls.append(args[2])
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Canvas, 'text', capture)
    picture = render_card(participant_card('month', 201))
    im = Image.open(BytesIO(picture))
    assert im.width + im.height <= 10000 and im.height / im.width <= 20
    assert len(picture) < 10*1024*1024
    assert 'Показаны 200 из 201 · полный список: /stats' in calls
