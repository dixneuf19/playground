"""JSON API, files and the room WebSocket."""

import contextlib
import hmac
import io
import json
import logging
import socket
import uuid
from typing import Any

import segno
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ValidationError

from karaoke.cancelled import cancel_reason
from karaoke.context import GuestName, Services, Svc, guest_token
from karaoke.hub import Client
from karaoke.messages import parse
from karaoke.photo_files import MAX_PHOTO_BYTES, PhotoRejectedError, image_type
from karaoke.photos import MAX_PENDING_PER_ROOM, MAX_PENDING_PER_UPLOADER

logger = logging.getLogger(__name__)

router = APIRouter()

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def lan_ip() -> str:
    """IP of the interface holding the default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def join_url(svc: Services, request: Request, code: str) -> str:
    if svc.cfg.public_url:
        return f"{svc.cfg.public_url}/r/{code}"
    url = request.base_url
    if url.hostname in LOOPBACK_HOSTS:
        # Screen opened on localhost: phones need the machine's LAN address.
        url = url.replace(hostname=lan_ip())
    return f"{str(url).rstrip('/')}/r/{code}"


async def room_or_404(svc: Services, code: str) -> str:
    if not await svc.store.room_exists(code):
        raise HTTPException(404, "Salle introuvable")
    return code.upper()


class CreateRoom(BaseModel):
    password: str = ""


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(svc: Svc) -> dict[str, str]:
    try:
        await svc.store.ping()
    except Exception as e:
        raise HTTPException(503, "database unreachable") from e
    return {"status": "ok"}


@router.get("/api/config")
async def config(svc: Svc) -> dict[str, Any]:
    return {"password_required": bool(svc.cfg.host_password)}


@router.post("/api/rooms")
async def create_room(body: CreateRoom, svc: Svc) -> dict[str, str]:
    password = svc.cfg.host_password
    if password and not hmac.compare_digest(body.password.encode(), password.encode()):
        raise HTTPException(403, "Mauvais mot de passe")
    code = await svc.store.create_room()
    logger.info("Created room %s", code)
    return {"code": code}


@router.get("/api/rooms/{code}")
async def get_room(code: str, request: Request, svc: Svc) -> dict[str, Any]:
    snapshot = await svc.store.snapshot(code)
    if snapshot is None:
        raise HTTPException(404, "Salle introuvable")
    return {**snapshot, "join_url": join_url(svc, request, snapshot["code"])}


@router.get("/api/rooms/{code}/qr.svg")
async def qr(code: str, request: Request, svc: Svc) -> Response:
    code = await room_or_404(svc, code)
    out = io.BytesIO()
    # Full SVG document: svg_inline() omits xmlns, which <img> renders blank.
    segno.make(join_url(svc, request, code), error="m").save(
        out, kind="svg", scale=10, border=2, dark="#120a24", light="#ffffff"
    )
    return Response(out.getvalue(), media_type="image/svg+xml")


@router.get("/api/search")
async def search(
    q: str, room: str, svc: Svc, everywhere: bool = False
) -> dict[str, Any]:
    """JSON search, for phone pages opened before the htmx one."""
    await room_or_404(svc, room)  # only guests of a live room search
    page = await svc.searcher.find(q, everywhere)
    cached = await svc.store.cached([r.video_id for r in page.results])
    return {
        "results": [
            {
                **r.to_dict(),
                "cached": r.video_id in cached,
                "cancelled": cancel_reason(r.artist or r.channel, r.title),
            }
            for r in page.results
        ],
        "strong": page.strong,
    }


@router.post("/api/rooms/{code}/photos")
async def upload_photo(
    code: str, request: Request, svc: Svc, name: GuestName, uploader: str = ""
) -> dict[str, str]:
    code = await room_or_404(svc, code)
    # The name this phone holds in the room wins; `?uploader=` is for phone
    # pages opened before the htmx one.
    uploader = name or uploader.strip()[:40] or "Anonyme"
    if int(request.headers.get("content-length") or 0) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo trop lourde")
    photo_id = uuid.uuid4().hex[:12]
    try:
        tmp = await svc.photos.receive(photo_id, request.stream())
    except PhotoRejectedError as e:
        raise HTTPException(e.status, e.message) from e
    try:
        if not await svc.store.add_photo(code, photo_id, uploader):
            raise HTTPException(
                429,
                f"Maximum {MAX_PENDING_PER_UPLOADER} photos en attente par "
                f"personne, {MAX_PENDING_PER_ROOM} par salle",
            )
        svc.photos.keep(tmp, code, photo_id)
    finally:
        tmp.unlink(missing_ok=True)
    await svc.store.notify_room(code)
    return {"id": photo_id}


@router.get("/photos/{code}/{photo_id}")
async def photo_file(code: str, photo_id: str, svc: Svc) -> Response:
    if not photo_id.isalnum() or not code.isalnum():
        raise HTTPException(404)
    path = svc.photos.path(code.upper(), photo_id)
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError as e:
        raise HTTPException(404) from e
    return FileResponse(
        path,
        media_type=image_type(head) or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/media/{video_id}.mp4")
async def media(video_id: str, svc: Svc) -> FileResponse:
    if not svc.library.exists(video_id):  # also rejects anything but a YouTube id
        raise HTTPException(404)
    return FileResponse(svc.library.path_for(video_id), media_type="video/mp4")


@router.websocket("/ws/{code}")
async def ws(
    websocket: WebSocket, code: str, role: str = "guest", view: str = "json"
) -> None:
    """Screens (role=display) and phones. The htmx phone page (view=html) gets
    rendered fragments and acts over HTTP; everyone else gets JSON states and
    sends JSON messages (karaoke.messages)."""
    svc: Services = websocket.app.state.services
    code = code.upper()
    client = Client(
        websocket,
        screen=role == "display",
        html=view == "html",
        token=guest_token(websocket),
    )
    await websocket.accept()
    try:
        if not await svc.hub.join(code, client):
            await websocket.close(code=4404)
            return
        while True:
            try:
                data = json.loads(await websocket.receive_text())
                if isinstance(data, dict):  # reactions are throttled per socket
                    data["sender"] = f"ws:{id(client)}"
                msg = parse(data)
            except (ValueError, ValidationError) as e:
                logger.warning("Bad message in %s: %s", code, e)
                continue
            await svc.actions.handle(code, msg, from_screen=client.screen)
    except WebSocketDisconnect:
        pass
    except Exception:
        # 1011: the page reconnects (htmx only does for 1006/1011/1012/1013)
        logger.exception("WebSocket of %s failed", code)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        svc.hub.leave(code, client)
        with contextlib.suppress(Exception):
            await websocket.close()
