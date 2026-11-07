"""Room state: one host screen, many guest phones, one shared queue.

Pure state, no I/O: karaoke.db loads a Room under a row lock, the app mutates
it with these methods, and karaoke.db saves it back.
"""

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from karaoke.photos import Photo, PhotoQueue

# No 0/O, 1/I/L: codes get typed from a TV across a dark room.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
ROOM_TTL_SECONDS = 24 * 3600

ItemStatus = Literal["pending", "downloading", "ready", "error"]
PlayerState = Literal["idle", "playing", "paused"]


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
    last_activity: float = field(default_factory=time.time)
    photos: PhotoQueue = field(default_factory=PhotoQueue)
    photo: Photo | None = None  # on screen right now
    # When the photo slot changes next (shown photo ends / gap before the next)
    photo_until: float | None = None
    # Playback position of the current song, as last reported by a screen:
    # `position` seconds at `position_at` (epoch), still running if playing.
    position: float = 0.0
    position_at: float | None = None

    def touch(self) -> None:
        self.last_activity = time.time()

    def find(self, item_id: str) -> QueueItem | None:
        if self.current and self.current.id == item_id:
            return self.current
        return next((i for i in self.queue if i.id == item_id), None)

    def add(self, item: QueueItem) -> None:
        self.queue.append(item)

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

    def played(self, now: float | None = None) -> float:
        """Where the current song is now, extrapolated while it plays."""
        if self.current is None:
            return 0.0
        if self.state == "playing" and self.position_at is not None:
            now = time.time() if now is None else now
            return self.position + max(0.0, now - self.position_at)
        return self.position

    def report_position(self, seconds: float, now: float | None = None) -> None:
        self.position = max(0.0, seconds)
        self.position_at = time.time() if now is None else now

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

    def finish(self, item_id: str) -> bool:
        """Called when a screen reports the end of a song. Idempotent across screens."""
        if self.current is None or self.current.id != item_id:
            return False
        self.advance()
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
