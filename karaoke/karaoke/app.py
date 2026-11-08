"""The app: wires the services and the routes. Safe to run several replicas:
all state is in Postgres (karaoke.db), files on a shared volume, and replicas
tell each other about changes with LISTEN/NOTIFY (karaoke.hub). Each replica
only keeps its own open sockets.

Every replica serves pages and phone actions (karaoke.actions). One of them,
the leader, also runs the background work (karaoke.worker)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from karaoke import api, pages
from karaoke.actions import Actions
from karaoke.config import Settings, settings
from karaoke.context import Services
from karaoke.db import Store
from karaoke.hub import Hub
from karaoke.library import Library
from karaoke.photo_files import PhotoFiles
from karaoke.search import Searcher
from karaoke.views import STATIC, render_guest_state
from karaoke.worker import Worker


async def start(cfg: Settings) -> Services:
    store = await Store.connect(cfg.dsn())
    library = Library(cfg.songs_dir, cfg.max_height)
    photos = PhotoFiles(cfg.data_dir)
    worker = Worker(cfg, store, library, photos)
    hub = Hub(cfg.dsn(), store, render_guest_state, on_downloads=worker.wake)
    searcher = Searcher(cfg.channels)
    svc = Services(
        cfg=cfg,
        store=store,
        library=library,
        photos=photos,
        searcher=searcher,
        actions=Actions(store, library, photos, searcher),
        hub=hub,
        worker=worker,
    )
    await hub.start()
    await worker.start()
    return svc


async def stop(svc: Services) -> None:
    await svc.hub.stop()
    await svc.worker.stop()
    await svc.store.close()


def create_app(cfg: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.services = await start(cfg)
        try:
            yield
        finally:
            await stop(app.state.services)

    app = FastAPI(title="Karaoke", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
