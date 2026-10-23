"""Small structured denial records. Never accept request bodies or credentials."""
import json
import logging
import re
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

logger = logging.getLogger('app.security')


def configure_security_audit():
    path = os.getenv('SECURITY_AUDIT_LOG')
    if path and not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)


def denied(surface, action, reason, *, actor_id=None, chat_id=None, status=None, request_id=None):
    record = {'time': datetime.now(timezone.utc).isoformat(), 'event': 'access_denied',
              'surface': surface, 'action': action, 'reason': reason}
    for key, value in [('actor_id', actor_id), ('chat_id', chat_id), ('status', status)]:
        if type(value) is int:
            record[key] = value
    if isinstance(request_id, str) and re.fullmatch(r'[0-9a-f]{32}', request_id):
        record['request_id'] = request_id
    logger.warning('%s', json.dumps(record, ensure_ascii=True, separators=(',', ':')))


def telegram_denied(event, action, reason='owner_required'):
    user = getattr(event, 'from_user', None)
    message = getattr(event, 'message', None) or event
    chat = getattr(message, 'chat', None)
    denied('telegram', action, reason, actor_id=getattr(user, 'id', None), chat_id=getattr(chat, 'id', None))
