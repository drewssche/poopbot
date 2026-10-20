from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest
from app.services import report_image_cache as cache
from app.services.weekly_report_card import ReportCard, Participant


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear_image_cache()
    yield
    cache.clear_image_cache()


def card():
    return ReportCard(date(2026,10,19), date(2026,10,25), 'Чат',
                      (1,0,0,0,0,0,0), (Participant('Аня',1),))


def test_simultaneous_requests_render_once_without_changing_pixels(monkeypatch):
    value = card()
    expected = cache.renderer.render_card(value)
    render = Mock(wraps=cache.renderer.render_card)
    monkeypatch.setattr(cache.renderer, 'render_card', render)
    key = cache.visual_fingerprint(value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: cache.shared_image(-1, value, key), range(4)))
    assert result == [expected]*4
    assert render.call_count == 1


def test_counts_names_periods_and_renderer_identity_invalidate_image(monkeypatch):
    value = card(); key = cache.visual_fingerprint(value)
    for changed in (replace(value, days=(2,0,0,0,0,0,0)),
                    replace(value, participants=(Participant('Борис',1),)),
                    replace(value, start=date(2026,10,26), end=date(2026,11,1)),
                    replace(value, label='Текущая неделя'), replace(value, chat_title='Другой чат')):
        assert cache.visual_fingerprint(changed) != key
    monkeypatch.setattr(cache, 'renderer_identity', lambda: 'new-font-or-layout')
    assert cache.visual_fingerprint(value) != key


def test_cache_is_bounded_and_scoped_to_chat(monkeypatch):
    monkeypatch.setattr(cache, 'MAX_IMAGES', 2)
    monkeypatch.setattr(cache, 'MAX_BYTES', 8)
    render = Mock(return_value=b'12345')
    monkeypatch.setattr(cache.renderer, 'render_card', render)
    value = card(); key = cache.visual_fingerprint(value)
    cache.shared_image(-1, value, key)
    cache.shared_image(-2, value, key)  # No cross-chat reuse; evicts previous bytes.
    cache.shared_image(-1, value, key)
    assert render.call_count == 3
    assert cache._bytes <= 8 and len(cache._images) <= 2
