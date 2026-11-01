"""Room state: one host screen, many guest phones, one shared queue.

Pure state, no I/O: the app layer runs downloads and broadcasts snapshots.
"""

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

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

    def advance(self) -> QueueItem | None:
        """Start the first ready song. Songs still downloading keep their place."""
        if self.current is not None:
            self.sung += 1
        self.current = None
        self.state = "idle"
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
        }


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create(self) -> Room:
        self.prune()
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._rooms:
                break
        room = Room(code=code)
        self._rooms[code] = room
        return room

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code.upper())

    def all(self) -> list[Room]:
        return list(self._rooms.values())

    def prune(self) -> None:
        cutoff = time.time() - ROOM_TTL_SECONDS
        for code in [c for c, r in self._rooms.items() if r.last_activity < cutoff]:
            del self._rooms[code]
