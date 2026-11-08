"""Photo files on the shared volume: `<data>/photos/<CODE>/<photo_id>`.

Which photos exist and in what order lives in Postgres (karaoke.db); this only
writes, serves and deletes the files.
"""

import asyncio
import os
import shutil
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from karaoke.db import RoomTx

MAX_PHOTO_BYTES = 10 * 1024 * 1024
FILE_GRACE_SECONDS = 5  # screens finish fading a photo out before its file goes
UPLOAD_MAX_AGE = 3600  # leftovers of interrupted uploads
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"RIFF": "image/webp",  # checked further for "WEBP" at offset 8
}


def image_type(data: bytes) -> str | None:
    for magic, mime in IMAGE_SIGNATURES.items():
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                return None
            return mime
    return None


class PhotoRejectedError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class PhotoFiles:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "photos"
        self.dir.mkdir(parents=True, exist_ok=True)
        # Uploads are written here first, then moved in once complete.
        self.upload_dir = data_dir / "tmp"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def path(self, code: str, photo_id: str) -> Path:
        return self.dir / code / photo_id

    async def receive(self, photo_id: str, chunks: AsyncIterator[bytes]) -> Path:
        """Stream an upload to a temporary file with a running count: never
        the whole body in memory, and cut off as soon as it is too big or not
        an image. The caller moves it in with `keep`, or deletes it."""
        tmp = self.upload_dir / photo_id
        size, head = 0, b""
        try:
            with tmp.open("wb") as out:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_PHOTO_BYTES:
                        raise PhotoRejectedError(413, "Photo trop lourde")
                    if len(head) < 12:
                        head = (head + chunk)[:12]
                        if len(head) == 12 and image_type(head) is None:
                            break
                    await asyncio.to_thread(out.write, chunk)
            if image_type(head) is None:
                raise PhotoRejectedError(415, "Format non supporté (JPEG, PNG ou WebP)")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    def keep(self, tmp: Path, code: str, photo_id: str) -> None:
        path = self.path(code, photo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, path)

    def forget(self, code: str, photo_ids: Iterable[str], delay: float = 0) -> None:
        paths = [self.path(code, i) for i in photo_ids]
        if not paths:
            return

        def delete() -> None:
            for path in paths:
                path.unlink(missing_ok=True)

        if delay:
            asyncio.get_running_loop().call_later(delay, delete)
        else:
            delete()

    def cleanup(self, tx: RoomTx | None) -> None:
        """Delete the files of the photos a room transaction let go of."""
        if tx is None or not tx.save:
            return
        self.forget(tx.room.code, tx.removed_photos)
        if tx.hidden_photo:
            self.forget(tx.room.code, [tx.hidden_photo], delay=FILE_GRACE_SECONDS)

    def prune(self, live_rooms: set[str]) -> None:
        for folder in self.dir.iterdir():
            if folder.name not in live_rooms:
                shutil.rmtree(folder, ignore_errors=True)
        limit = time.time() - UPLOAD_MAX_AGE
        for leftover in self.upload_dir.iterdir():
            if leftover.stat().st_mtime < limit:
                leftover.unlink(missing_ok=True)
