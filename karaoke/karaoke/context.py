"""The services a request needs, built once per app (see karaoke.app)."""

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends
from starlette.requests import HTTPConnection

from karaoke.actions import Actions
from karaoke.config import Settings
from karaoke.db import Store
from karaoke.hub import Hub
from karaoke.library import Library
from karaoke.messages import clean_text
from karaoke.photo_files import PhotoFiles
from karaoke.search import Searcher
from karaoke.worker import Worker

# The name confirmed for a room lives in a cookie of its own; the last name
# used anywhere is only offered as a suggestion when joining another room.
LAST_NAME_COOKIE = "karaoke_name"
MAX_NAME = 40


def room_cookie(code: str) -> str:
    return f"karaoke_name_{code.upper()}"


def _cookie(conn: HTTPConnection, key: str) -> str:
    # Cleaned like messages.Name, so the cookie and a song's singer match.
    return clean_text(unquote(conn.cookies.get(key, "")), MAX_NAME)


@dataclass
class Services:
    cfg: Settings
    store: Store
    library: Library
    photos: PhotoFiles
    searcher: Searcher
    actions: Actions
    hub: Hub
    worker: Worker


def services(conn: HTTPConnection) -> Services:
    return conn.app.state.services


def guest_name(conn: HTTPConnection) -> str:
    """The name the phone confirmed for this room (set by guest.js), or ""."""
    code = conn.path_params.get("code", "")
    return _cookie(conn, room_cookie(code)) if code else ""


def last_name(conn: HTTPConnection) -> str:
    """The name this phone used last, in any room: a suggestion only."""
    return _cookie(conn, LAST_NAME_COOKIE)


Svc = Annotated[Services, Depends(services)]
GuestName = Annotated[str, Depends(guest_name)]
LastName = Annotated[str, Depends(last_name)]
