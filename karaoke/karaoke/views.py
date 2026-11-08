"""HTML rendering: Jinja templates, escaped automatically.

The phone page is rendered here in full, then kept up to date with fragments
pushed over its WebSocket (karaoke.hub) and swapped in by htmx.
"""

import hashlib
from functools import cache
from pathlib import Path
from typing import Any, cast

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from karaoke.reactions import EFFECTS, EMOJI
from karaoke.rooms import Room

HERE = Path(__file__).parent
STATIC = HERE / "static"

env = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
templates = Jinja2Templates(env=env)


@cache
def static_url(name: str) -> str:
    """A URL that changes with the file, so a deploy never serves stale JS."""
    digest = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
    return f"/static/{name}?v={digest}"


def plural(n: int, word: str, suffix: str = "s") -> str:
    return f"{n} {word}{suffix if n > 1 else ''}"


def duration(seconds: int | None) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""


# Jinja types its globals and filters too narrowly for plain functions.
cast(Any, env.globals).update(static=static_url, reactions=EMOJI, effects=EFFECTS)
cast(Any, env.filters).update(plural=plural, duration=duration)


def guest_state(room: Room, me: str) -> dict[str, Any]:
    """What the phone page shows of the room, from this guest's point of view."""
    upcoming = room.photos.upcoming()
    return {
        "room": room,
        "me": me,
        "photos_total": len(upcoming),
        # (place in the photo queue, photo), only this guest's own
        "my_photos": [(n, p) for n, p in enumerate(upcoming, 1) if p.uploader == me],
        "photo_is_mine": bool(room.photo and me and room.photo.uploader == me),
    }


def render_guest_state(room: Room, me: str) -> str:
    """The fragments pushed to a phone; each replaces its element by id."""
    return env.get_template("_guest_push.html").render(
        **guest_state(room, me), oob=True
    )
