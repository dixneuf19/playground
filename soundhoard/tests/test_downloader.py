import json
from pathlib import Path

from soundhoard.downloader import DownloadRegistry, video_url


class TestVideoUrl:
    def test_video_url(self) -> None:
        assert video_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


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

    def test_stale_entry_cleans_up_json(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))
        registry.register("abc123", "/nonexistent/song.mp3", "My Song")

        # Trigger stale cleanup
        registry.check("abc123")

        # Verify it's gone from the persisted file too
        data = json.loads((tmp_path / "downloads.json").read_text())
        assert "abc123" not in data

    def test_multiple_entries(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        file_a = tmp_path / "a.mp3"
        file_b = tmp_path / "b.mp3"
        file_a.touch()
        file_b.touch()

        registry.register("id_a", str(file_a), "Song A")
        registry.register("id_b", str(file_b), "Song B")

        assert registry.check("id_a") == "Song A"
        assert registry.check("id_b") == "Song B"
