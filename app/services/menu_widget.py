"""Render Telegram command menus as paged private widgets."""

from contextvars import ContextVar
from functools import lru_cache
from html import unescape
import json
import re

from app.services.daily_widget_demo import ASSETS


# Set by scheduled notices which have no keyboard but are later edited in place.
scheduled_widget_notice = ContextVar("scheduled_widget_notice", default=False)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=1)
def layout():
    logo = (ASSETS / "brand.svg").read_text().strip()
    return (ASSETS / "menu.html").read_text().replace("__LOGO__", logo)


def chunks(text, budget):
    """Split on lines or words when possible, preserving all visible text."""
    while len(encode(text).encode()) > budget:
        end = min(budget, len(text))
        while len(encode(text[:end]).encode()) > budget:
            end -= 1
        boundary = text.rfind("\n", 1, end + 1)
        if boundary < end // 2:
            boundary = text.rfind(" ", 1, end + 1)
        cut = boundary if boundary > 0 else end
        yield text[:cut].rstrip()
        text = text[cut:].lstrip()
    yield text


def make_pages(text, buttons):
    if any(a.startswith("help:about:") for _, a in buttons) and text.startswith(
        "🤖 О боте"
    ):
        owner = buttons[0][1].rsplit(":", 1)[-1]
        buttons = [
            ["Проект на GitHub", "https://github.com/drewssche/poopbot"],
            ["Назад", "help:back:" + owner],
        ]
        text = text.partition("Проект на GitHub:")[0]
    plain = unescape(
        re.sub(r"</?(?:b|i|strong|em|code|u|a)(?:\s[^>]*)?>", "", text)
    ).replace("`", "")
    heading, _, body = plain.strip().partition("\n")
    title = next(chunks(heading, 105))
    body = (heading[len(title) :] + "\n" + body).strip()
    pages = [{"t": title, "b": part, "buttons": []} for part in chunks(body, 420)]
    # Keep a destructive confirmation behind its full explanation.
    index = (
        len(pages) - 1
        if any(action.startswith("help:delete_confirm") for _, action in buttons)
        else 0
    )
    for label, action in buttons:
        page = pages[index]
        if (
            len(page["buttons"]) >= 5
            or len(encode(public_buttons(page["buttons"] + [[label, action]])).encode())
            > 390
        ):
            index += 1
            if index == len(pages):
                pages.append({"t": title, "b": "", "buttons": []})
        pages[index]["buttons"].append([label, action])
    return pages


def public_buttons(buttons):
    return [
        [label, action] if action.startswith("https://") else [label]
        for label, action in buttons
    ]


def page_state(pages, revision, index=0):
    page = pages[index]
    return dict(
        v=revision,
        p=index,
        n=len(pages),
        t=page["t"],
        b=page["b"],
        a=public_buttons(page["buttons"]),
    )
