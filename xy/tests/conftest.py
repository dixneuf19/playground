"""Postgres for the tests: XY_TEST_DATABASE_URL if set (CI), otherwise a
throwaway postgres container started once per test session."""

import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from xy.app import create_app
from xy.config import Settings

IMAGE = "postgres:18-alpine"


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
    if url := os.environ.get("XY_TEST_DATABASE_URL"):
        _wait_for(url)
        yield url
        return
    if shutil.which("docker") is None:
        pytest.skip("needs Postgres: set XY_TEST_DATABASE_URL or install docker")
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


ROUND = """
title: Les fromages
date: 2026-01-01
axes:
  x: { neg: Doux, pos: Fort }
  y: { neg: Quotidien, pos: Grandes occasions }
candidates:
  - { id: comte, name: Comté, emoji: "🧀" }
  - { id: brie, name: Brie }
  - { id: munster, name: Munster }
"""

FUTURE = """
title: Plus tard
date: 2999-01-01
axes:
  x: { neg: A, pos: B }
  y: { neg: C, pos: D }
candidates:
  - { id: a, name: A }
  - { id: b, name: B }
"""


@pytest.fixture
def rounds_dir(tmp_path: Path) -> Path:
    (tmp_path / "fromages.yaml").write_text(ROUND, encoding="utf-8")
    (tmp_path / "plus-tard.yaml").write_text(FUTURE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(database_url: str, rounds_dir: Path) -> Iterator[TestClient]:
    async def reset() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute("DROP TABLE IF EXISTS answers")
        finally:
            await conn.close()

    asyncio.run(reset())
    app = create_app(Settings(database_url=database_url, rounds_dir=rounds_dir))
    with TestClient(app) as c:
        yield c
