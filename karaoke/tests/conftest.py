"""Postgres for the tests: KARAOKE_TEST_DATABASE_URL if set (CI), otherwise a
throwaway postgres container started once per test session."""

import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from karaoke.app import create_app
from karaoke.config import Settings
from karaoke.context import Services
from karaoke.reactions import Kind

IMAGE = "postgres:18-alpine"


def services(client: TestClient) -> Services:
    return cast(FastAPI, client.app).state.services


def freeze_reactions(client: TestClient) -> None:
    """Reaction buckets stop refilling, so a test can count exact caps."""
    reactions = services(client).actions.reactions
    command = reactions.command

    def frozen(code: str, sender: str, kind: Kind, count: int, _now: float):
        return command(code, sender, kind, count, 0.0)

    setattr(reactions, "command", frozen)  # noqa: B010


def claim(client: TestClient, code: str, name: str) -> TestClient:
    """Claim the name in the room, as the name sheet does (a client is one
    phone: claiming another name renames it)."""
    res = client.post(f"/r/{code}/name", data={"name": name})
    assert res.status_code == 204, res.text
    return client


def phone(client: TestClient) -> TestClient:
    """Another phone in the same app: its own cookies, the same event loop."""
    other = TestClient(client.app, base_url=str(client.base_url))
    other.portal = client.portal
    return other


def _wait_for(url: str, timeout: float = 30) -> None:
    async def ping() -> None:
        conn = await asyncpg.connect(url)
        await conn.close()

    deadline = time.monotonic() + timeout
    while True:
        try:
            asyncio.run(ping())
            return
        except (OSError, asyncpg.PostgresError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if url := os.environ.get("KARAOKE_TEST_DATABASE_URL"):
        _wait_for(url)
        yield url
        return
    if shutil.which("docker") is None:
        pytest.skip("needs Postgres: set KARAOKE_TEST_DATABASE_URL or install docker")
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-e",
            "POSTGRES_PASSWORD=test",
            "-p",
            "127.0.0.1::5432",
            IMAGE,
        ],
        text=True,
    ).strip()
    try:
        port = (
            subprocess.check_output(
                ["docker", "port", container, "5432/tcp"], text=True
            )
            .strip()
            .rsplit(":", 1)[1]
        )
        url = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
        _wait_for(url)
        yield url
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)


@pytest.fixture
def make_settings(database_url: str, tmp_path: Path) -> Callable[..., Settings]:
    async def reset() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(
                "DROP TABLE IF EXISTS guests, plays, photos, queue_items, videos, "
                "rooms, favourites, "
                "schema_migrations CASCADE"
            )
        finally:
            await conn.close()

    asyncio.run(reset())

    def make(**overrides: object) -> Settings:
        values: dict = {"data_dir": tmp_path, "database_url": database_url}
        values.update(overrides)
        return Settings(**values)  # type: ignore[arg-type]

    return make


@pytest.fixture
def client(make_settings: Callable[..., Settings]) -> Iterator[TestClient]:
    app = create_app(
        # http: an https public URL makes cookies `secure`, and the test
        # client opens WebSockets over plain ws://
        make_settings(host_password="secret", public_url="http://k.example")
    )
    with TestClient(app) as c:
        # No YouTube in unit tests: downloads never start.
        services(c).worker.download_videos = False
        yield c
