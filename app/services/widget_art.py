"""Offline artwork exported from the Mini App's Figma assets."""
from base64 import b64encode
from functools import lru_cache

from app.services.daily_widget_demo import ASSETS


@lru_cache
def brand_data():
    return b64encode((ASSETS / "figma-logo.webp").read_bytes()).decode("ascii")


def brand_image():
    return f'<img class="brand" alt="PoopCheck" src="data:image/webp;base64,{brand_data()}">'
