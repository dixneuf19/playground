import json
import os
from pathlib import Path

import pytest
from mutagen.mp3 import MP3

from soundhoard.downloader import DownloadRegistry, _track_from_entry, download_single


class TestTrackFromEntry:
    def test_youtube_single(self) -> None:
        track = _track_from_entry(
            {
                "id": "dQw4w9WgXcQ",
                "title": "Never Gonna Give You Up",
                "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "extractor_key": "Youtube",
            }
        )
        assert track is not None
        assert track.key == "youtube:dQw4w9WgXcQ"
        assert track.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert track.label == "Never Gonna Give You Up"

    def test_youtube_playlist_entry(self) -> None:
        track = _track_from_entry(
            {
                "_type": "url",
                "id": "Vh4O04Bpovw",
                "title": "Some Video",
                "url": "https://www.youtube.com/watch?v=Vh4O04Bpovw",
                "ie_key": "Youtube",
            }
        )
        assert track is not None
        assert track.key == "youtube:Vh4O04Bpovw"
        assert track.url == "https://www.youtube.com/watch?v=Vh4O04Bpovw"

    def test_soundcloud_single(self) -> None:
        track = _track_from_entry(
            {
                "id": "293",
                "title": "Flickermood",
                "webpage_url": "https://soundcloud.com/forss/flickermood",
                "extractor_key": "Soundcloud",
            }
        )
        assert track is not None
        assert track.key == "soundcloud:293"
        assert track.title == "Flickermood"

    def test_soundcloud_set_entry_has_no_title(self) -> None:
        # Flat extraction of a SoundCloud set yields url_transparent entries
        # with a null title
        track = _track_from_entry(
            {
                "_type": "url_transparent",
                "id": "290",
                "title": None,
                "url": "https://soundcloud.com/forss/city-ports",
                "ie_key": "Soundcloud",
            }
        )
        assert track is not None
        assert track.key == "soundcloud:290"
        assert track.title is None
        assert track.label == "https://soundcloud.com/forss/city-ports"

    def test_resolved_entry_prefers_webpage_url_over_stream_url(self) -> None:
        # A fully resolved track puts an expiring media stream in `url`
        track = _track_from_entry(
            {
                "id": "293",
                "title": "Flickermood",
                "url": "https://playback.media-streaming.soundcloud.cloud/x/playlist.m3u8?expires=1",
                "webpage_url": "https://soundcloud.com/forss/flickermood",
                "extractor_key": "Soundcloud",
            }
        )
        assert track is not None
        assert track.url == "https://soundcloud.com/forss/flickermood"

    def test_keys_do_not_collide_across_sources(self) -> None:
        youtube = _track_from_entry(
            {"id": "293", "url": "https://example.com/a", "ie_key": "Youtube"}
        )
        soundcloud = _track_from_entry(
            {"id": "293", "url": "https://example.com/b", "ie_key": "Soundcloud"}
        )
        assert youtube is not None and soundcloud is not None
        assert youtube.key != soundcloud.key

    def test_fallback_url_for_single_without_webpage_url(self) -> None:
        track = _track_from_entry(
            {"id": "293", "ie_key": "Soundcloud"},
            fallback_url="https://soundcloud.com/forss/flickermood",
        )
        assert track is not None
        assert track.url == "https://soundcloud.com/forss/flickermood"

    def test_entry_without_id_is_skipped(self) -> None:
        assert _track_from_entry({"url": "https://example.com/x"}) is None

    def test_entry_without_url_is_skipped(self) -> None:
        assert _track_from_entry({"id": "abc"}) is None

    def test_unknown_extractor(self) -> None:
        track = _track_from_entry({"id": "abc", "url": "https://example.com/x"})
        assert track is not None
        assert track.key == "unknown:abc"


class TestDownloadRegistry:
    def test_register_and_check(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        # Create a fake downloaded file
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry.register("youtube:abc123", str(fake_file), "My Song")

        assert registry.check("youtube:abc123") == "My Song"

    def test_check_unknown_track(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))
        assert registry.check("youtube:unknown") is None

    def test_check_stale_entry(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        # Register with a file that doesn't exist
        registry.register("youtube:abc123", "/nonexistent/song.mp3", "My Song")

        # Should return None and clean up the stale entry
        assert registry.check("youtube:abc123") is None
        assert "youtube:abc123" not in registry._data

    def test_persistence(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry1 = DownloadRegistry(str(tmp_path))
        registry1.register("youtube:abc123", str(fake_file), "My Song")

        # New instance should load from disk
        registry2 = DownloadRegistry(str(tmp_path))
        assert registry2.check("youtube:abc123") == "My Song"

    def test_json_format(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()

        registry = DownloadRegistry(str(tmp_path))
        registry.register("youtube:abc123", str(fake_file), "My Song")

        data = json.loads((tmp_path / "downloads.json").read_text())
        assert data["youtube:abc123"]["title"] == "My Song"
        assert data["youtube:abc123"]["filename"] == str(fake_file)

    def test_stale_entry_cleans_up_json(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))
        registry.register("youtube:abc123", "/nonexistent/song.mp3", "My Song")

        # Trigger stale cleanup
        registry.check("youtube:abc123")

        # Verify it's gone from the persisted file too
        data = json.loads((tmp_path / "downloads.json").read_text())
        assert "youtube:abc123" not in data

    def test_multiple_entries(self, tmp_path: Path) -> None:
        registry = DownloadRegistry(str(tmp_path))

        file_a = tmp_path / "a.mp3"
        file_b = tmp_path / "b.mp3"
        file_a.touch()
        file_b.touch()

        registry.register("youtube:id_a", str(file_a), "Song A")
        registry.register("soundcloud:id_b", str(file_b), "Song B")

        assert registry.check("youtube:id_a") == "Song A"
        assert registry.check("soundcloud:id_b") == "Song B"

    def test_legacy_keys_are_migrated_to_youtube(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "song.mp3"
        fake_file.touch()
        (tmp_path / "downloads.json").write_text(
            json.dumps({"dQw4w9WgXcQ": {"filename": str(fake_file), "title": "Old"}})
        )

        registry = DownloadRegistry(str(tmp_path))

        assert registry.check("youtube:dQw4w9WgXcQ") == "Old"
        data = json.loads((tmp_path / "downloads.json").read_text())
        assert "dQw4w9WgXcQ" not in data
        assert data["youtube:dQw4w9WgXcQ"]["title"] == "Old"


@pytest.mark.integration
class TestDownloadSingle:
    """Integration tests that download real tracks. Slow, requires network.

    Run with: uv run pytest -m integration
    """

    def _assert_valid_mp3(self, tmp_path: Path, filename: str) -> MP3:
        # File exists and is an mp3
        assert Path(filename).exists()
        assert filename.endswith(".mp3")

        # No leftover files (only mp3 + no webm/webp/json)
        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert files[0].endswith(".mp3")

        # Metadata is set
        m = MP3(filename)
        assert m.tags is not None
        assert m.tags.get("TIT2") is not None  # title
        assert m.tags.get("TPE1") is not None  # artist
        assert m.tags.get("TALB") is not None  # album (from uploader)
        assert any("PIC" in k for k in m.tags)  # cover art embedded
        return m

    def test_youtube_download_and_metadata(self, tmp_path: Path) -> None:
        # Rick Astley - Never Gonna Give You Up
        downloaded = download_single(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", str(tmp_path)
        )

        assert downloaded.title
        self._assert_valid_mp3(tmp_path, downloaded.path)

    def test_soundcloud_download_and_metadata(self, tmp_path: Path) -> None:
        # Forss - Flickermood (Creative Commons)
        downloaded = download_single(
            "https://soundcloud.com/forss/flickermood", str(tmp_path)
        )

        assert downloaded.title == "Flickermood"
        self._assert_valid_mp3(tmp_path, downloaded.path)
