import json
from pathlib import Path

import pytest

from soundhoard.downloader import DownloadRegistry, extract_video_id


class TestExtractVideoId:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLfoo", "dQw4w9WgXcQ"),
            ("check this out https://youtu.be/dQw4w9WgXcQ cool right?", "dQw4w9WgXcQ"),
            ("http://youtube.com/watch?v=abc-_123DEf", "abc-_123DEf"),
        ],
    )
    def test_valid_urls(self, url: str, expected: str) -> None:
        assert extract_video_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com",
            "not a url",
            "https://vimeo.com/12345",
            "https://youtube.com/channel/UCxyz",
            "",
        ],
    )
    def test_invalid_urls(self, url: str) -> None:
        assert extract_video_id(url) is None


class TestDownloadRegistry:
    def test_register_and_check(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        # Create a fake downloaded file
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry.register("abc123", str(fake_file), "My Song")

        assert registry.check("abc123") == "My Song"

    def test_check_unknown_video(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))
        assert registry.check("unknown") is None

    def test_check_stale_entry(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        # Register with a file that doesn't exist
        registry.register("abc123", "/nonexistent/song.mp3", "My Song")

        # Should return None and clean up the stale entry
        assert registry.check("abc123") is None
        assert "abc123" not in registry._data

    def test_persistence(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry1 = DownloadRegistry(str(tmp_path))
        registry1.register("abc123", str(fake_file), "My Song")

        # New instance should load from disk
        registry2 = DownloadRegistry(str(tmp_path))
        assert registry2.check("abc123") == "My Song"

    def test_json_format(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry = DownloadRegistry(str(tmp_path))
        registry.register("abc123", str(fake_file), "My Song")

        data = json.loads((tmp_path / "downloads.json").read_text())
        assert data["abc123"]["title"] == "My Song"
        assert data["abc123"]["filename"] == str(fake_file)
