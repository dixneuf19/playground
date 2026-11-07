"""Photos sent from phones to the big screen, one at a time, fairly mixed.

Pure state, no I/O. Fairness: the next photo comes from the uploader whose
last photo was shown the longest ago, and someone who never had a photo shown
goes first. Ties go to whoever uploaded first. So when Léa sends 10 photos and
Tom then sends 1, Tom's shows right after Léa's first, not after all 10.
"""

import itertools
import uuid
from dataclasses import dataclass, field
from typing import Any

MAX_PENDING_PER_UPLOADER = 30
# Names are free text, so the per-person limit alone does not cap the disk.
MAX_PENDING_PER_ROOM = 300

_upload_counter = itertools.count()


@dataclass
class Photo:
    uploader: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Upload order, to break ties and keep each uploader's photos in order.
    seq: int = field(default_factory=lambda: next(_upload_counter))

    def to_dict(self) -> dict[str, Any]:
        # Anonymous on purpose: guessing who sent a photo is part of the fun.
        # Phones keep the ids of their own uploads to list and cancel them.
        return {"id": self.id}


@dataclass
class PhotoQueue:
    pending: list[Photo] = field(default_factory=list)
    # uploader -> turn number when their last photo was shown
    last_shown: dict[str, int] = field(default_factory=dict)
    turn: int = 0

    def can_add(self, uploader: str) -> bool:
        mine = sum(1 for p in self.pending if p.uploader == uploader)
        return mine < MAX_PENDING_PER_UPLOADER

    def add(self, photo: Photo) -> None:
        self.pending.append(photo)

    def remove(self, photo_id: str, uploader: str) -> Photo | None:
        """Cancel a pending photo; only its uploader may."""
        for p in self.pending:
            if p.id == photo_id and p.uploader == uploader:
                self.pending.remove(p)
                return p
        return None

    def _priority(self, photo: Photo) -> tuple[int, int]:
        return (self.last_shown.get(photo.uploader, -1), photo.seq)

    def pop_next(self) -> Photo | None:
        if not self.pending:
            return None
        # Each uploader's oldest pending photo competes; lowest priority wins.
        heads: dict[str, Photo] = {}
        for p in self.pending:
            heads.setdefault(p.uploader, p)
        chosen = min(heads.values(), key=self._priority)
        self.pending.remove(chosen)
        self.last_shown[chosen.uploader] = self.turn
        self.turn += 1
        return chosen

    def upcoming(self) -> list[Photo]:
        """The order photos would show in if nobody uploads anything else."""
        sim = PhotoQueue(list(self.pending), dict(self.last_shown), self.turn)
        order = []
        while (p := sim.pop_next()) is not None:
            order.append(p)
        return order
