"""The app: pages, LiveKit tokens and the show socket.

Video never goes through here: phones publish to a LiveKit server and screens
subscribe to it. This app hands out the tokens and runs the show (coco_lanta.hub).
"""

import hashlib
import hmac
import io
import json
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import cache
from pathlib import Path
from typing import Any

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from coco_lanta.config import Settings, settings
from coco_lanta.hub import EMOJI, NAME_MAX, SIDES, Client, Hub, clean
from coco_lanta.show import TEAMS
from coco_lanta.tokens import access_token

HERE = Path(__file__).parent
STATIC = HERE / "static"
ROLES = ("cam", "tv", "regie", "react")  # of a socket
PAGES = {"": "guest", "tv": "tv", "regie": "regie", "affiche": "poster"}
MOVED = ("cam", "react")  # QR codes of the first version
UNAUTHORIZED = 4401  # close code: the control room asks for the password

templates = Jinja2Templates(directory=HERE / "templates")


@cache
def static_url(name: str) -> str:
    """A URL that changes with the file, so a deploy never serves stale JS."""
    digest = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
    return f"/static/{name}?v={digest}"


class CamRequest(BaseModel):
    name: str


def create_app(cfg: Settings = settings) -> FastAPI:
    hub = Hub()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(title="Coco Lanta", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.hub = hub
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def base_url(request: Request) -> str:
        return cfg.public_url or str(request.base_url).rstrip("/")

    def page(request: Request, name: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            f"{name}.html",
            {
                "sides": SIDES,
                "emoji": [e for e in EMOJI if e not in SIDES],
                "teams": TEAMS,
                "static": static_url,
                "cfg": cfg,
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/qr.svg")
    async def qr(request: Request) -> Response:
        """One QR code for every guest: react, and film if they want to."""
        out = io.BytesIO()
        segno.make(f"{base_url(request)}/", error="m").save(
            out, kind="svg", scale=10, border=2, dark="#0e3b2c", light="#fff7e2"
        )
        return Response(out.getvalue(), media_type="image/svg+xml")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/{name}", response_class=HTMLResponse)
    async def any_page(request: Request, name: str = "") -> Response:
        if name in MOVED:
            return RedirectResponse("/")
        if name not in PAGES:
            raise HTTPException(404)
        return page(request, PAGES[name])

    @app.post("/api/cam")
    async def cam(body: CamRequest) -> dict[str, str]:
        """A phone becomes a camera: a LiveKit token that may only publish."""
        name = clean(body.name, NAME_MAX)
        if not name:
            raise HTTPException(422, "Il faut un nom")
        identity = f"cam-{secrets.token_hex(4)}"
        return {
            "id": identity,
            "url": cfg.livekit_url,
            "token": access_token(cfg, identity, name, publish=True),
        }

    @app.post("/api/viewer")
    async def viewer() -> dict[str, str]:
        """A screen (TV or control room): a LiveKit token that may only watch."""
        identity = f"screen-{secrets.token_hex(4)}"
        return {
            "url": cfg.livekit_url,
            "token": access_token(cfg, identity, "screen", publish=False),
        }

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        query = socket.query_params
        role = query.get("role", "react")
        if role not in ROLES:
            await socket.close()
            return
        await socket.accept()
        if (
            role == "regie"
            and cfg.regie_password
            and not hmac.compare_digest(
                query.get("key", "").encode(), cfg.regie_password.encode()
            )
        ):
            await socket.close(code=UNAUTHORIZED)
            return
        client = Client(socket, role)
        name = clean(query.get("name"), NAME_MAX)
        if role == "cam":
            client.cam_id = query.get("id", "")[:32]
            if not client.cam_id.startswith("cam-") or not name:
                await socket.close()
                return
        await hub.join(client, name)
        try:
            while True:
                msg: Any = json.loads(await socket.receive_text())
                if isinstance(msg, dict):
                    await hub.handle(client, msg)
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            await hub.leave(client)

    return app
