"""HTML pages, and the endpoints the htmx phone page talks to."""

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError

from karaoke.api import room_or_404
from karaoke.cancelled import cancel_reason
from karaoke.context import (
    LAST_NAME_COOKIE,
    TOKEN_AGE,
    TOKEN_COOKIE,
    GuestName,
    LastName,
    Services,
    Svc,
    cookie_text,
    guest_token,
    new_token,
    room_cookie,
)
from karaoke.messages import MAX_NAME, Add, clean_text, from_guest, parse
from karaoke.rooms import Room
from karaoke.search import SearchPage
from karaoke.views import guest_state, render_guest_state, templates

router = APIRouter()

NO_CACHE = {"Cache-Control": "no-cache"}


def page(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, headers=NO_CACHE)


async def results(svc: Svc, room: Room, me: str, found: SearchPage) -> dict[str, Any]:
    """What `_results.html` needs: the search results, or before any search
    the songs popular across rooms."""
    popular = [] if found.query else await svc.store.popular()
    songs = [(r.video_id, r.artist or r.channel, r.title) for r in found.results]
    songs += [(p.video_id, p.artist, p.title) for p in popular]
    mine = [room.current, *room.queue] if room.current else room.queue
    return {
        "code": room.code,
        "page": found,
        "popular": popular,
        "cached": await svc.store.cached([vid for vid, _, _ in songs]),
        "cancelled": {vid: cancel_reason(a, t) for vid, a, t in songs},
        "added": {i.video_id for i in mine if i.singer == me},
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return page(request, "index.html")


@router.get("/display/{code}", response_class=HTMLResponse)
async def display(code: str, request: Request, svc: Svc) -> HTMLResponse:
    await room_or_404(svc, code)
    return page(request, "display.html")


def _set_cookies(
    svc: Services, response: Response, code: str, token: str, name: str = ""
) -> None:
    # The token is a bearer credential for the phone's names.
    secure = svc.cfg.public_url.startswith("https://")
    response.set_cookie(
        TOKEN_COOKIE,
        token,
        max_age=TOKEN_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    if name:
        response.set_cookie(
            LAST_NAME_COOKIE,
            quote(name),
            max_age=TOKEN_AGE,
            samesite="lax",
            secure=secure,
        )
    response.delete_cookie(room_cookie(code))


@router.get("/r/{code}", response_class=HTMLResponse)
async def guest(
    code: str, request: Request, svc: Svc, me: GuestName, last: LastName
) -> HTMLResponse:
    room = await svc.store.load_room(code)
    if room is None:
        raise HTTPException(404, "Salle introuvable")
    token = guest_token(request) or new_token()
    # A name confirmed before names were claimed: claim it once, if still free.
    old = cookie_text(request, room_cookie(room.code))
    if not me and old and await svc.actions.claim_name(room.code, token, old):
        me = old
    response = page(
        request,
        "guest.html",
        **guest_state(room, me),
        oob=False,
        suggested=me or old or last,
        **await results(svc, room, me, await svc.searcher.find("", everywhere=False)),
        initial=True,  # the search intro, inside the page
    )
    _set_cookies(svc, response, room.code, token)
    return response


@router.post("/r/{code}/name")
async def claim_name(code: str, request: Request, svc: Svc) -> Response:
    """The name sheet: one phone per name in a room. A name is free when
    nobody holds it, or when its phone has been gone for a while."""
    code = code.upper()
    name = clean_text((await request.form()).get("name"), MAX_NAME)
    if not name:
        raise HTTPException(422, "Choisis un prénom")
    token = guest_token(request) or new_token()
    claimed = await svc.actions.claim_name(code, token, name)
    if claimed is None:
        raise HTTPException(404, "Salle introuvable")
    if not claimed:
        return JSONResponse(
            {
                "detail": f"«\u00a0{name}\u00a0» est déjà pris dans cette salle",
                "suggestion": await svc.store.suggest_name(code, name),
            },
            status_code=409,
        )
    response = Response(status_code=204)
    _set_cookies(svc, response, code, token, name)
    return response


@router.get("/r/{code}/state", response_class=HTMLResponse)
async def guest_fragments(code: str, svc: Svc, me: GuestName) -> HTMLResponse:
    """The pushed fragments, on demand (after a drag held pushes back)."""
    room = await svc.store.load_room(code)
    if room is None:
        raise HTTPException(404, "Salle introuvable")
    return HTMLResponse(render_guest_state(room, me), headers=NO_CACHE)


@router.get("/r/{code}/search", response_class=HTMLResponse)
async def search(
    code: str,
    request: Request,
    svc: Svc,
    me: GuestName,
    q: str = "",
    everywhere: int = 0,
) -> HTMLResponse:
    room = await svc.store.load_room(code)  # only guests of a live room search
    if room is None:
        raise HTTPException(404, "Salle introuvable")
    found = await svc.searcher.find(q, bool(everywhere))
    return templates.TemplateResponse(
        request, "_results.html", await results(svc, room, me, found)
    )


@router.post("/r/{code}/do")
async def act(code: str, request: Request, svc: Svc, me: GuestName) -> Response:
    """Every button of the phone page. The new state comes back by push."""
    code = await room_or_404(svc, code)
    data: dict[str, Any] = dict(await request.form())
    data["sender"] = me  # the reaction throttle key
    try:
        msg = from_guest(parse(data), me)
    except ValidationError as e:
        raise HTTPException(422, "Requête invalide") from e
    room = await svc.actions.handle(code, msg, from_screen=False)
    headers = {}
    if isinstance(msg, Add) and room is not None:
        place = room.position_of(msg.video_id, msg.singer or "Anonyme")
        if place is None:
            raise HTTPException(409, "La file est pleine")
        text = (
            "Ajouté, c'est bientôt à toi 🎤"
            if place <= 1
            else f"Ajouté, position {place}"
        )
        headers["HX-Trigger"] = json.dumps({"toast": text})
    return Response(status_code=204, headers=headers)
