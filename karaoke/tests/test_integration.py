"""Hits YouTube for real: run with `uv run pytest -m integration -v`."""

from pathlib import Path

import pytest

from karaoke.config import Settings
from karaoke.library import Library
from karaoke.search import search_channels


@pytest.mark.integration
def test_search_finds_french_catalogue() -> None:
    results = search_channels("renaud manhattan kaboul", Settings().channels)
    assert results
    assert results[0].title == "Manhattan-Kaboul"
    assert results[0].artist == "Renaud"


@pytest.mark.integration
def test_download_produces_playable_mp4(tmp_path: Path) -> None:
    library = Library(tmp_path, max_height=360)
    progress: list[float] = []
    title, size = library.download("QkvTWXmcZEs", "test", progress.append)
    path = library.path_for("QkvTWXmcZEs")
    assert library.exists("QkvTWXmcZEs")
    assert path.read_bytes()[4:8] == b"ftyp"
    assert size == path.stat().st_size
    assert title
    assert progress[-1] == 1.0
    assert progress == sorted(progress)
