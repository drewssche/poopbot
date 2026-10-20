"""Photo-question state shared with the lightweight Mini App (no bot/Pillow imports)."""
from html import escape
from html.parser import HTMLParser
import json
from sqlalchemy import select, update
from app.db.models import WeeklyReport


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(value)
    return ''.join(parser.parts)


def units(value):
    return len(plain_text(value).encode('utf-16-le')) // 2


def question_caption(text):
    """Keep whole HTML lines/mentions; large groups use /app for the full list."""
    if units(text) <= 1024:
        return text
    suffix = '\n\nПолный список и отметки: /app'
    kept = []
    for line in text.splitlines():
        candidate = '\n'.join(kept + [line]) + suffix
        if units(candidate) > 1024:
            break
        kept.append(line)
    if not kept:
        # Defensive fallback for a single oversized name/line. Never cut an HTML tag.
        raw = plain_text(text)
        while len(raw.encode('utf-16-le')) // 2 > 1024 - units(suffix):
            raw = raw[:-1]
        return escape(raw) + suffix
    return '\n'.join(kept).rstrip() + suffix


def queue_question_view(db, chat_id, message_id, text, reply_markup):
    """Return True for a photo Q1. Its single publisher owns caption AND media."""
    row_id = db.scalar(select(WeeklyReport.id).where(
        WeeklyReport.source_chat_id == chat_id,
        WeeklyReport.photo_message_id == message_id,
        WeeklyReport.q1_session_id.is_not(None),
        WeeklyReport.disabled.is_(False)))
    if row_id is None:
        return False
    markup = reply_markup.model_dump(exclude_none=True) if hasattr(reply_markup, 'model_dump') else reply_markup
    flow = chat_id > 0 and any(
        str(button.get('callback_data', '')).startswith(('q2:', 'q3:'))
        for buttons in (markup or {}).get('inline_keyboard', []) for button in buttons)
    db.execute(update(WeeklyReport).where(WeeklyReport.id == row_id).values(
        q1_flow_text=text if flow else None,
        q1_flow_markup=json.dumps(markup, ensure_ascii=False) if flow else None,
        revision=WeeklyReport.revision + 1, attempts=0, retry_at=None))
    return True


def queue_photo_questions(db, payloads):
    """Let the report publisher refresh photo Q1s; retain legacy text/Q2/Q3 work."""
    return [p for p in payloads if not queue_question_view(
        db, p['chat_id'], p['message_id'], p['text'], p.get('reply_markup'))]
