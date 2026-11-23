import datetime as dt
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xy import rounds
from xy.config import Settings
from xy.db import Store

STATIC = Path(__file__).parent / "static"

Coord = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]


class Position(BaseModel):
    x: Coord
    y: Coord


class Answers(BaseModel):
    positions: dict[str, Position] = Field(min_length=1, max_length=40)


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or Settings()
    all_rounds = rounds.load(cfg.rounds_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.store = await Store.connect(cfg.dsn)
        try:
            yield
        finally:
            await app.state.store.close()

    app = FastAPI(title="XY", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def store(request: Request) -> Store:
        return request.app.state.store

    def get_round(round_id: str) -> rounds.Round:
        r = all_rounds.get(round_id)
        if r is None or r.date > dt.date.today():
            raise HTTPException(404, "Manche inconnue")
        return r

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/rounds")
    async def list_rounds() -> list[dict]:
        return [
            {"id": r.id, "title": r.title, "date": r.date.isoformat()}
            for r in rounds.published(all_rounds, dt.date.today())
        ]

    @app.get("/api/rounds/{round_id}")
    async def show_round(round_id: str) -> rounds.Round:
        return get_round(round_id)

    @app.get("/api/rounds/{round_id}/results")
    async def results(round_id: str, request: Request) -> dict:
        get_round(round_id)
        s = store(request)
        return {
            "players": await s.players(round_id),
            "candidates": [asdict(c) for c in await s.results(round_id)],
        }

    @app.put("/api/rounds/{round_id}/answers/{player}", status_code=204)
    async def answer(
        round_id: str, player: uuid.UUID, body: Answers, request: Request
    ) -> Response:
        r = get_round(round_id)
        unknown = set(body.positions) - r.candidate_ids()
        if unknown:
            raise HTTPException(
                422, f"Candidats inconnus : {', '.join(sorted(unknown))}"
            )
        await store(request).save(
            round_id, player, {c: (p.x, p.y) for c, p in body.positions.items()}
        )
        return Response(status_code=204)

    return app
