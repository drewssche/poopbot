"""Render Telegram command menus as paged private widgets."""

from contextvars import ContextVar
from functools import lru_cache
from html import unescape
import json
import re

from app.services.widget_art import brand_image
from app.services.daily_widget_demo import ASSETS


# Set by scheduled notices which have no keyboard but are later edited in place.
scheduled_widget_notice = ContextVar("scheduled_widget_notice", default=False)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=1)
def layout():
    logo = brand_image()
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


def make_pages(text, buttons, *, embedded=False):
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
    if embedded:
        whole = [{"t": title, "b": body, "buttons": buttons}]
        if len(buttons) <= 5 and len(encode(journal_menu_page(whole, "0" * 16)).encode()) <= 980:
            return whole
    pages = [{"t": title, "b": part, "buttons": []} for part in chunks(body, 320 if embedded else 420)]
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
            > (240 if embedded else 390)
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

def journal_menu_page(pages, revision, index=0):
    page = pages[index]
    def key(p, i=None):
        return f"m:{revision}:{p}" + (f":{i}" if i is not None else "")

    nav = []
    up = None
    for p, part in enumerate(pages):
        for i, (label, action) in enumerate(part["buttons"]):
            if label.removeprefix("⬅️ ").strip() == "Назад":
                up = key(p, i)
            if p != index or action == "stats:noop":
                continue
            if action.startswith("https://t.me/") and action.endswith("?startapp"):
                target = "app"
            else:
                target = action if action.startswith("https://") else key(p, i)
            nav.append([label, target])
    if index:
        nav.append([f"‹ {index}/{len(pages)}", key(index - 1)])
    if index + 1 < len(pages):
        nav.append([f"Далее · {index + 2}/{len(pages)} ›", key(index + 1)])
    return dict(title=page["t"], body=page["b"], nav=nav, active="command", up=up or "back")
