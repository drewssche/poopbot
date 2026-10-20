"""Bounded, shared PNG cache; fingerprints also survive process restarts in the outbox."""
from collections import OrderedDict
from dataclasses import asdict
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock

from app.services import weekly_report_card as renderer

MAX_IMAGES = 32
MAX_BYTES = 4 * 1024 * 1024
_images = OrderedDict()
_bytes = 0
_lock = RLock()


@lru_cache(maxsize=1)
def renderer_identity():
    # A design/font/asset update must invalidate persisted fingerprints too.
    digest = sha256(Path(renderer.__file__).read_bytes())
    for path in sorted(renderer.ASSETS.iterdir()):
        if path.suffix in {'.png', '.ttf'}:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def visual_fingerprint(card):
    # Quality answers and per-message buttons/captions are absent from ReportCard.
    value = json.dumps([renderer_identity(), asdict(card)], ensure_ascii=False,
                       sort_keys=True, separators=(',', ':'), default=str)
    return sha256(value.encode()).hexdigest()


def shared_image(chat_id, card, fingerprint):
    global _bytes
    key = (chat_id, fingerprint)
    # Publication and refresh can render on different threads. Only render once.
    with _lock:
        if key in _images:
            _images.move_to_end(key)
            return _images[key]
        picture = renderer.render_card(card)
        if len(picture) <= MAX_BYTES:
            while _images and (len(_images) >= MAX_IMAGES or _bytes + len(picture) > MAX_BYTES):
                _, previous = _images.popitem(last=False)
                _bytes -= len(previous)
            _images[key] = picture
            _bytes += len(picture)
        return picture


def clear_image_cache():
    global _bytes
    with _lock:
        _images.clear()
        _bytes = 0
