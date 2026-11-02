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
    source = Path(renderer.__file__)
    digest = sha256(source.read_bytes())
    digest.update(source.with_name('report_layouts.py').read_bytes())
    digest.update(source.with_name('report_animals.py').read_bytes())
    for path in sorted(renderer.ASSETS.iterdir()):
        if path.suffix in {'.png', '.ttf'}:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def visual_fingerprint(card):
    # Weekly visuals include quality, comparison and rank; daily visuals do not.
    # Per-message text/buttons remain outside the image fingerprint.
    fields = asdict(card)
    fields.pop('chat_title')  # Only present in the message caption.
    if card.layout == 'daily' or (card.layout == 'summary' and (card.period == 'week' or card.private)):
        fields['participants'] = len(card.participants)
    if card.layout == 'daily':
        fields.pop('label')
        fields.pop('details')
        if card.private:
            fields['participants'] = 0
    elif card.layout == 'weekly' or (card.layout == 'summary' and card.period in ('month', 'year') and not card.private):
        from app.services.report_layouts import MAX_REPORT_PARTICIPANTS
        fields['participant_count'] = len(card.participants)
        fields['participants'] = fields['participants'][:3 if card.private else MAX_REPORT_PARTICIPANTS]
    value = json.dumps([renderer_identity(), fields], ensure_ascii=False,
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
