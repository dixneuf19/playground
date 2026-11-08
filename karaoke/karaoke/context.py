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
from karaoke.photo_files import PhotoFiles
from karaoke.search import Searcher
from karaoke.worker import Worker

NAME_COOKIE = "karaoke_name"
MAX_NAME = 40


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
    """The name the phone picked, kept in a cookie (set by guest.js)."""
    return unquote(conn.cookies.get(NAME_COOKIE, "")).strip()[:MAX_NAME]


Svc = Annotated[Services, Depends(services)]
GuestName = Annotated[str, Depends(guest_name)]
