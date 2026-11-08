"""Room state: one host screen, many guest phones, one shared queue.

Pure state, no I/O: karaoke.db loads a Room under a row lock, karaoke.actions
and karaoke.worker mutate it with these methods, and karaoke.db saves it back.
Methods that may change nothing return whether they did, so a no-op is neither
saved nor pushed to the phones.
"""

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from karaoke.cancelled import cancel_reason
from karaoke.photos import Photo, PhotoQueue

# No 0/O, 1/I/L: codes get typed from a TV across a dark room.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
ROOM_TTL_SECONDS = 24 * 3600
MAX_QUEUE = 200
PHOTO_SECONDS = 10
PHOTO_GAP_SECONDS = 1.5

ItemStatus = Literal["pending", "downloading", "ready", "error"]
PlayerState = Literal["idle", "playing", "paused"]


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _now(now: float | None) -> float:
    return time.time() if now is None else now


@dataclass
class QueueItem:
    video_id: str
    title: str
    artist: str
    singer: str
    thumbnail: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: ItemStatus = "pending"
    progress: float = 0.0
    # Set server-side from karaoke.cancelled, never trusted from the phone.
    cancelled: str | None = None

    @classmethod
    def new(
        cls,
        video_id: str,
        title: str,
        artist: str,
        singer: str,
        status: ItemStatus = "pending",
    ) -> "QueueItem":
        """A song a guest asked for. The thumbnail is built here, not taken
        from the phone: every screen loads it."""
        return cls(
            video_id=video_id,
            title=title,
            artist=artist,
            singer=singer or "Anonyme",
            thumbnail=thumbnail_url(video_id),
            status=status,
            cancelled=cancel_reason(artist, title),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "title": self.title,
            "artist": self.artist,
            "singer": self.singer,
            "thumbnail": self.thumbnail,
            "status": self.status,
            "progress": round(self.progress, 2),
            "cancelled": self.cancelled,
        }


@dataclass
class Room:
    code: str
    queue: list[QueueItem] = field(default_factory=list)
    current: QueueItem | None = None
    state: PlayerState = "idle"
    volume: float = 0.9
    sung: int = 0
    photos: PhotoQueue = field(default_factory=PhotoQueue)
    photo: Photo | None = None  # on screen right now
    # When the photo slot changes next (shown photo ends / gap before the next)
    photo_until: float | None = None
    # Playback position of the current song, as last reported by a screen:
    # `position` seconds at `position_at` (epoch), still running if playing.
    position: float = 0.0
    position_at: float | None = None

    # --- queue --------------------------------------------------------------

    @property
    def full(self) -> bool:
        return len(self.queue) >= MAX_QUEUE

    def add(self, item: QueueItem) -> None:
        self.queue.append(item)

    def enqueue(self, item: QueueItem) -> bool:
        """Queue a guest's song, and start it if nothing plays and it's ready."""
        if self.full:
            return False
        self.add(item)
        self.start_if_idle()
        return True

    def remove(self, item_id: str) -> bool:
        before = len(self.queue)
        self.queue = [i for i in self.queue if i.id != item_id]
        return len(self.queue) != before

    def move(self, item_id: str, index: int) -> bool:
        item = next((i for i in self.queue if i.id == item_id), None)
        if item is None:
            return False
        self.queue.remove(item)
        index = max(0, min(index, len(self.queue)))
        self.queue.insert(index, item)
        return True

    def position_of(self, video_id: str, singer: str) -> int | None:
        """1-based place of this singer's latest request for this video, the
        current song being 1."""
        items = ([self.current] if self.current else []) + self.queue
        places = [
            n
            for n, i in enumerate(items, 1)
            if i.video_id == video_id and i.singer == singer
        ]
        return places[-1] if places else None

    # --- playback -----------------------------------------------------------

    def played(self, now: float | None = None) -> float:
        """Where the current song is now, extrapolated while it plays."""
        if self.current is None:
            return 0.0
        if self.state == "playing" and self.position_at is not None:
            return self.position + max(0.0, _now(now) - self.position_at)
        return self.position

    def report_position(self, seconds: float, now: float | None = None) -> None:
        self.position = max(0.0, seconds)
        self.position_at = _now(now)

    def advance(self) -> QueueItem | None:
        """Start the first ready song. Songs still downloading keep their place."""
        if self.current is not None:
            self.sung += 1
        self.current = None
        self.state = "idle"
        self.position, self.position_at = 0.0, None
        for item in self.queue:
            if item.status == "ready":
                self.queue.remove(item)
                self.current = item
                self.state = "playing"
                break
        return self.current

    def start_if_idle(self) -> bool:
        """Start a ready song if nothing plays (a download just finished)."""
        if self.current is not None:
            return False
        return self.advance() is not None

    def finish(self, item_id: str) -> bool:
        """Called when a screen reports the end of a song. Idempotent across screens."""
        if self.current is None or self.current.id != item_id:
            return False
        self.advance()
        return True

    def pause(self, now: float | None = None) -> bool:
        if self.current is None:
            return False
        self.report_position(self.played(now), now)
        self.state = "paused"
        return True

    def play(self, now: float | None = None) -> bool:
        if self.current is None:
            return False
        if self.state != "playing":
            self.report_position(self.position, now)
        self.state = "playing"
        return True

    def restart(self, now: float | None = None) -> bool:
        if self.current is None:
            return False
        self.report_position(0, now)
        self.state = "playing"
        return True

    def set_volume(self, value: float) -> None:
        self.volume = max(0.0, min(1.0, value))

    def screen_report(
        self, state: PlayerState, seconds: float | None, now: float | None = None
    ) -> bool:
        """A screen says its player started or stopped (the video's own
        controls, autoplay, end of the "Au tour de…" countdown)."""
        if self.current is None or state == "idle":
            return False
        self.state = state
        if seconds is not None:
            self.report_position(seconds, now)
        return True

    # --- photos -------------------------------------------------------------

    def _hide_photo(self, now: float) -> None:
        self.photo = None
        self.photo_until = now + PHOTO_GAP_SECONDS

    def skip_photo(self, now: float | None = None) -> bool:
        if self.photo is None:
            return False
        self._hide_photo(_now(now))
        return True

    def remove_photo(self, photo_id: str, uploader: str) -> bool:
        return self.photos.remove(photo_id, uploader) is not None

    def rotate_photo(self, now: float | None = None) -> bool:
        """Advance the photo slot once its timer ran out: hide the photo on
        screen, then after a short gap show the next one."""
        now = _now(now)
        if self.photo_until and self.photo_until > now:
            return False  # changed meanwhile (photo skipped)
        if self.photo is not None:
            self._hide_photo(now)
        elif (photo := self.photos.pop_next()) is not None:
            self.photo = photo
            self.photo_until = now + PHOTO_SECONDS
        else:
            self.photo_until = None
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "current": self.current.to_dict() if self.current else None,
            "queue": [i.to_dict() for i in self.queue],
            "state": self.state,
            "volume": self.volume,
            "sung": self.sung,
            "position": round(self.played(), 2),
            "photo": self.photo.to_dict() if self.photo else None,
            "photos": [p.to_dict() for p in self.photos.upcoming()],
        }


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
