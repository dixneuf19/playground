"""The services a request needs, built once per app (see karaoke.app)."""

import re
import secrets
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
from karaoke.messages import MAX_NAME, clean_text
from karaoke.photo_files import PhotoFiles
from karaoke.search import Searcher
from karaoke.worker import Worker

# A random id per phone, set by the server: whoever holds it owns the names it
# claimed (table `guests`). The name cookies are only suggestions.
TOKEN_COOKIE = "karaoke_guest"
TOKEN_AGE = 365 * 24 * 3600
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
# The last name used anywhere, offered when joining another room.
LAST_NAME_COOKIE = "karaoke_name"


def room_cookie(code: str) -> str:
    """Where pages from before names were claimed kept the room's name. Read
    once to claim it for the phone, then deleted."""
    return f"karaoke_name_{code.upper()}"


def cookie_text(conn: HTTPConnection, key: str) -> str:
    # Cleaned like messages.Name, so the cookie and a song's singer match.
    return clean_text(unquote(conn.cookies.get(key, "")), MAX_NAME)


def new_token() -> str:
    return secrets.token_hex(16)


def guest_token(conn: HTTPConnection) -> str:
    """This phone's token, or "" when it has none (yet)."""
    token = conn.cookies.get(TOKEN_COOKIE, "")
    return token if _TOKEN.match(token) else ""


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


async def guest_name(conn: HTTPConnection) -> str:
    """The name this phone holds in the room, or ""."""
    code = conn.path_params.get("code", "")
    token = guest_token(conn)
    if not code or not token:
        return ""
    return await services(conn).store.guest_name(code, token)


def last_name(conn: HTTPConnection) -> str:
    """The name this phone used last, in any room: a suggestion only."""
    return cookie_text(conn, LAST_NAME_COOKIE)


Svc = Annotated[Services, Depends(services)]
GuestName = Annotated[str, Depends(guest_name)]
LastName = Annotated[str, Depends(last_name)]
