"""Signed group selectors; possession never replaces Telegram membership checks."""
import hashlib
import hmac
import re


def group_scope(token: str, chat_id: int) -> str:
    if chat_id >= 0:
        raise ValueError('Group required')
    value = f'g{abs(chat_id)}'
    digest = hmac.new(token.encode(), ('mini-app-scope:' + value).encode(), hashlib.sha256).hexdigest()[:24]
    return f'{value}_{digest}'


def scope_chat(token: str, value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'g[1-9][0-9]{0,15}_[0-9a-f]{24}', value):
        raise ValueError('Invalid group link')
    chat_id = -int(value.split('_')[0][1:])
    if not hmac.compare_digest(value, group_scope(token, chat_id)):
        raise ValueError('Invalid group link')
    return chat_id
