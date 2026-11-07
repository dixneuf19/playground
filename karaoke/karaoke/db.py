"""Postgres-backed state shared by every replica.

A room is loaded under a row lock (`Store.room_tx`), mutated with the pure
methods of karaoke.rooms / karaoke.photos, then saved in the same transaction,
which also NOTIFYs every replica so they push the new state to their sockets.
Videos (download status, cache index) and photos live in their own tables.
"""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from karaoke.photos import (
    MAX_PENDING_PER_ROOM,
    MAX_PENDING_PER_UPLOADER,
    Photo,
    PhotoQueue,
)
from karaoke.rooms import QueueItem, Room, generate_code

logger = logging.getLogger(__name__)

CHANNEL = "karaoke"
SCHEMA_LOCK = 4242_0001  # pg_advisory_xact_lock keys
EVICT_LOCK = 4242_0002
STALE_DOWNLOAD_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    code          text PRIMARY KEY,
    state         text NOT NULL DEFAULT 'idle',
    volume        real NOT NULL DEFAULT 0.9,
    sung          int  NOT NULL DEFAULT 0,
    photo_id      text,
    photo_until   timestamptz,
    photo_turn    int  NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_activity timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS position real NOT NULL DEFAULT 0;
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS position_at timestamptz;
-- Which song `position` belongs to: reports for another song are ignored.
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS current_item_id text;
CREATE TABLE IF NOT EXISTS videos (
    video_id   text PRIMARY KEY,
    status     text   NOT NULL DEFAULT 'pending',
    progress   real   NOT NULL DEFAULT 0,
    title      text   NOT NULL DEFAULT '',
    size       bigint NOT NULL DEFAULT 0,
    last_used  timestamptz NOT NULL DEFAULT now(),
    claimed_by text,
    heartbeat  timestamptz
);
CREATE TABLE IF NOT EXISTS queue_items (
    id         text PRIMARY KEY,
    room_code  text NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
    position   int  NOT NULL,
    is_current boolean NOT NULL DEFAULT false,
    video_id   text NOT NULL,
    title      text NOT NULL DEFAULT '',
    artist     text NOT NULL DEFAULT '',
    singer     text NOT NULL DEFAULT '',
    thumbnail  text NOT NULL DEFAULT '',
    cancelled  text
);
CREATE INDEX IF NOT EXISTS queue_items_room  ON queue_items (room_code);
CREATE INDEX IF NOT EXISTS queue_items_video ON queue_items (video_id);
CREATE TABLE IF NOT EXISTS photos (
    id         text PRIMARY KEY,
    room_code  text NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
    uploader   text NOT NULL,
    seq        bigserial,
    state      text NOT NULL DEFAULT 'pending',
    shown_turn int
);
CREATE INDEX IF NOT EXISTS photos_room ON photos (room_code, state);
"""


@dataclass
class RoomTx:
    """A locked room inside a transaction. Set `save = False` to discard."""

    conn: asyncpg.Connection
    room: Room
    save: bool = True
    commands: list[dict[str, Any]] = field(default_factory=list)
    # Filled on save: photos that left the queue without being shown (cancelled)
    removed_photos: list[str] = field(default_factory=list)
    _loaded_pending: set[str] = field(default_factory=set)
    _loaded_current: str | None = None

    def command(self, action: str, **extra: Any) -> None:
        """Send to the room's screens, on whichever replica they are connected."""
        self.commands.append({"action": action, **extra})


class Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        store = cls(pool)
        await store.migrate()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK)
            await conn.execute(SCHEMA)

    async def ping(self) -> None:
        await self.pool.fetchval("SELECT 1")

    # --- notifications ------------------------------------------------------

    @staticmethod
    async def _notify(conn: asyncpg.Connection, payload: dict[str, Any]) -> None:
        await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, json.dumps(payload))

    async def notify_room(self, code: str) -> None:
        async with self.pool.acquire() as conn:
            await self._notify(conn, {"room": code})

    # --- rooms --------------------------------------------------------------

    async def create_room(self) -> str:
        while True:
            code = await self.pool.fetchval(
                "INSERT INTO rooms (code) VALUES ($1) ON CONFLICT DO NOTHING RETURNING code",
                generate_code(),
            )
            if code:
                return code

    async def room_exists(self, code: str) -> bool:
        return bool(
            await self.pool.fetchval(
                "SELECT 1 FROM rooms WHERE code = $1", code.upper()
            )
        )

    async def snapshot(self, code: str) -> dict[str, Any] | None:
        # One consistent view across the room, queue and photo queries.
        async with (
            self.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            room = await self._load(conn, code.upper(), lock=None)
        return room.snapshot() if room else None

    async def save_position(self, code: str, item_id: str, seconds: float) -> None:
        """Periodic report from a screen: no lock and no notify, nobody needs
        a push for it. Ignored once the song is no longer the current one."""
        await self.pool.execute(
            """UPDATE rooms SET position = $3, position_at = now()
               WHERE code = $1 AND current_item_id = $2""",
            code.upper(),
            item_id,
            max(0.0, seconds),
        )

    @asynccontextmanager
    async def room_tx(
        self, code: str, *, skip_locked: bool = False
    ) -> AsyncGenerator[RoomTx | None]:
        """Yield the locked room (None if missing, or already locked with
        skip_locked), save it and notify every replica on clean exit."""
        async with self.pool.acquire() as conn, conn.transaction():
            lock = "FOR UPDATE SKIP LOCKED" if skip_locked else "FOR UPDATE"
            room = await self._load(conn, code.upper(), lock=lock)
            if room is None:
                yield None
                return
            tx = RoomTx(
                conn,
                room,
                _loaded_pending={p.id for p in room.photos.pending},
                _loaded_current=room.current.id if room.current else None,
            )
            yield tx
            if not tx.save:
                return
            await self._save(tx)
            await self._notify(conn, {"room": room.code})
            for cmd in tx.commands:
                await self._notify(conn, {"room": room.code, "command": cmd})

    async def _load(
        self, conn: asyncpg.Connection, code: str, lock: str | None
    ) -> Room | None:
        row = await conn.fetchrow(
            f"""SELECT code, state, volume, sung, photo_id, photo_turn,
                       extract(epoch FROM photo_until)::float8 AS photo_until,
                       position,
                       extract(epoch FROM position_at)::float8 AS position_at,
                       extract(epoch FROM last_activity)::float8 AS last_activity
                FROM rooms WHERE code = $1 {lock or ""}""",
            code,
        )
        if row is None:
            return None
        items = await conn.fetch(
            """SELECT q.*, coalesce(v.status, 'pending') AS status,
                      coalesce(v.progress, 0) AS progress
               FROM queue_items q LEFT JOIN videos v USING (video_id)
               WHERE q.room_code = $1 ORDER BY q.is_current DESC, q.position""",
            code,
        )
        queue, current = [], None
        for r in items:
            item = QueueItem(
                video_id=r["video_id"],
                title=r["title"],
                artist=r["artist"],
                singer=r["singer"],
                thumbnail=r["thumbnail"],
                id=r["id"],
                status=r["status"],
                progress=r["progress"],
                cancelled=r["cancelled"],
            )
            if r["is_current"]:
                current = item
            else:
                queue.append(item)
        photos = await conn.fetch(
            """SELECT id, uploader, seq, state, shown_turn FROM photos
               WHERE room_code = $1 ORDER BY seq""",
            code,
        )
        pending, last_shown, on_screen = [], {}, None
        for p in photos:
            photo = Photo(uploader=p["uploader"], id=p["id"], seq=p["seq"])
            if p["state"] == "pending":
                pending.append(photo)
            elif p["shown_turn"] is not None:
                last_shown[p["uploader"]] = max(
                    last_shown.get(p["uploader"], -1), p["shown_turn"]
                )
            if p["id"] == row["photo_id"]:
                on_screen = photo
        return Room(
            code=row["code"],
            queue=queue,
            current=current,
            state=row["state"],
            volume=row["volume"],
            sung=row["sung"],
            last_activity=row["last_activity"],
            photos=PhotoQueue(pending, last_shown, row["photo_turn"]),
            photo=on_screen,
            photo_until=row["photo_until"],
            position=row["position"],
            position_at=row["position_at"],
        )

    async def _save(self, tx: RoomTx) -> None:
        conn, room = tx.conn, tx.room
        await conn.execute(
            """UPDATE rooms SET state = $2, volume = $3, sung = $4, photo_id = $5,
                   photo_until = to_timestamp($6), photo_turn = $7,
                   position = $8, position_at = to_timestamp($9),
                   current_item_id = $10, last_activity = now()
               WHERE code = $1""",
            room.code,
            room.state,
            room.volume,
            room.sung,
            room.photo.id if room.photo else None,
            room.photo_until,
            room.photos.turn,
            room.position,
            room.position_at,
            room.current.id if room.current else None,
        )
        await conn.execute("DELETE FROM queue_items WHERE room_code = $1", room.code)
        rows = [(room.current, -1, True)] if room.current else []
        rows += [(item, i, False) for i, item in enumerate(room.queue)]
        await conn.executemany(
            """INSERT INTO queue_items (id, room_code, position, is_current, video_id,
                   title, artist, singer, thumbnail, cancelled)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            [
                (
                    i.id,
                    room.code,
                    pos,
                    cur,
                    i.video_id,
                    i.title,
                    i.artist,
                    i.singer,
                    i.thumbnail,
                    i.cancelled,
                )
                for i, pos, cur in rows
            ],
        )
        if room.current and room.current.id != tx._loaded_current:
            await conn.execute(
                "UPDATE videos SET last_used = now() WHERE video_id = $1",
                room.current.video_id,
            )
        # Photos that left the pending queue: shown (now on screen) or cancelled
        remaining = {p.id for p in room.photos.pending}
        for photo_id in tx._loaded_pending - remaining:
            if room.photo and room.photo.id == photo_id:
                await conn.execute(
                    "UPDATE photos SET state = 'shown', shown_turn = $2 WHERE id = $1",
                    photo_id,
                    room.photos.turn - 1,
                )
            else:
                await conn.execute("DELETE FROM photos WHERE id = $1", photo_id)
                tx.removed_photos.append(photo_id)

    async def prune_rooms(self, ttl_seconds: int) -> list[str]:
        rows = await self.pool.fetch(
            """DELETE FROM rooms
               WHERE last_activity < now() - make_interval(secs => $1)
               RETURNING code""",
            ttl_seconds,
        )
        return [r["code"] for r in rows]

    async def room_codes(self) -> set[str]:
        return {r["code"] for r in await self.pool.fetch("SELECT code FROM rooms")}

    # --- photos -------------------------------------------------------------

    async def add_photo(self, code: str, photo_id: str, uploader: str) -> bool:
        """Queue a photo unless its uploader, or the room, has too many waiting."""
        added = await self.pool.fetchval(
            """INSERT INTO photos (id, room_code, uploader)
               SELECT $1, $2, $3
               WHERE (SELECT count(*) FROM photos
                      WHERE room_code = $2 AND uploader = $3 AND state = 'pending') < $4
                 AND (SELECT count(*) FROM photos
                      WHERE room_code = $2 AND state = 'pending') < $5
               RETURNING id""",
            photo_id,
            code,
            uploader,
            MAX_PENDING_PER_UPLOADER,
            MAX_PENDING_PER_ROOM,
        )
        return added is not None

    async def rooms_due_for_photo(self) -> list[str]:
        rows = await self.pool.fetch(
            """SELECT code FROM rooms r
               WHERE (photo_until IS NULL OR photo_until <= now())
                 AND (photo_id IS NOT NULL OR EXISTS (
                     SELECT 1 FROM photos p
                     WHERE p.room_code = r.code AND p.state = 'pending'))"""
        )
        return [r["code"] for r in rows]

    # --- videos -------------------------------------------------------------

    async def video_status(self, video_id: str) -> str:
        await self.pool.execute(
            "INSERT INTO videos (video_id) VALUES ($1) ON CONFLICT DO NOTHING", video_id
        )
        return await self.pool.fetchval(
            "SELECT status FROM videos WHERE video_id = $1", video_id
        )

    async def reset_video(self, video_id: str) -> None:
        """Download again: after an error, or when the file went missing."""
        await self.pool.execute(
            """UPDATE videos SET status = 'pending', progress = 0, claimed_by = NULL
               WHERE video_id = $1 AND status IN ('ready', 'error')""",
            video_id,
        )

    async def claim_download(self, video_id: str, worker: str) -> bool:
        claimed = await self.pool.fetchval(
            """UPDATE videos SET status = 'downloading', progress = 0,
                   claimed_by = $2, heartbeat = now()
               WHERE video_id = $1 AND (status = 'pending' OR (
                   status = 'downloading'
                   AND heartbeat < now() - make_interval(secs => $3)))
               RETURNING video_id""",
            video_id,
            worker,
            STALE_DOWNLOAD_SECONDS,
        )
        return claimed is not None

    async def heartbeat(
        self, video_id: str, worker: str, progress: float | None
    ) -> None:
        await self.pool.execute(
            """UPDATE videos SET heartbeat = now(), progress = coalesce($3, progress)
               WHERE video_id = $1 AND claimed_by = $2 AND status = 'downloading'""",
            video_id,
            worker,
            progress,
        )

    async def finish_download(
        self, video_id: str, worker: str, ok: bool, title: str = "", size: int = 0
    ) -> bool:
        """Record the outcome, unless another replica took the download over
        meanwhile (then its own outcome counts). A finished file still lands in
        the cache: renames are atomic, so it is complete either way."""
        done = await self.pool.fetchval(
            """UPDATE videos SET status = $3, progress = $4, title = $5, size = $6,
                   last_used = now(), claimed_by = NULL
               WHERE video_id = $1 AND claimed_by = $2
               RETURNING video_id""",
            video_id,
            worker,
            "ready" if ok else "error",
            1.0 if ok else 0.0,
            title,
            size,
        )
        return done is not None

    async def release_downloads(self, worker: str) -> None:
        """Shutting down: hand unfinished downloads to the other replicas now
        rather than after the stale delay."""
        await self.pool.execute(
            """UPDATE videos SET status = 'pending', progress = 0, claimed_by = NULL
               WHERE claimed_by = $1 AND status = 'downloading'""",
            worker,
        )

    async def restore_queued_videos(self) -> list[str]:
        """Safety net for queued songs whose video row went missing (evicted at
        the moment they were added): recreate them as pending. Returns the
        queued videos marked ready, for the caller to check their files."""
        await self.pool.execute(
            """INSERT INTO videos (video_id)
               SELECT DISTINCT video_id FROM queue_items
               ON CONFLICT DO NOTHING"""
        )
        rows = await self.pool.fetch(
            """SELECT DISTINCT v.video_id FROM videos v
               JOIN queue_items q USING (video_id) WHERE v.status = 'ready'"""
        )
        return [r["video_id"] for r in rows]

    async def idle_rooms_with_ready_song(self) -> list[str]:
        """Rooms with nothing playing although a queued song is ready (a
        replica died between finishing a download and starting the song)."""
        rows = await self.pool.fetch(
            """SELECT DISTINCT q.room_code FROM queue_items q
               JOIN videos v USING (video_id)
               WHERE v.status = 'ready' AND NOT q.is_current
                 AND NOT EXISTS (SELECT 1 FROM queue_items c
                                 WHERE c.room_code = q.room_code AND c.is_current)"""
        )
        return [r["room_code"] for r in rows]

    async def downloads_to_resume(self) -> list[str]:
        """Queued videos nobody is downloading: new, or whose worker died."""
        rows = await self.pool.fetch(
            """SELECT DISTINCT v.video_id FROM videos v
               JOIN queue_items q USING (video_id)
               WHERE v.status = 'pending' OR (v.status = 'downloading'
                   AND v.heartbeat < now() - make_interval(secs => $1))""",
            STALE_DOWNLOAD_SECONDS,
        )
        return [r["video_id"] for r in rows]

    async def rooms_with_video(self, video_id: str) -> list[str]:
        rows = await self.pool.fetch(
            "SELECT DISTINCT room_code FROM queue_items WHERE video_id = $1", video_id
        )
        return [r["room_code"] for r in rows]

    async def cached(self, video_ids: list[str]) -> set[str]:
        rows = await self.pool.fetch(
            "SELECT video_id FROM videos WHERE status = 'ready' AND video_id = ANY($1)",
            video_ids,
        )
        return {r["video_id"] for r in rows}

    async def evict(self, max_bytes: int) -> list[str]:
        """Forget the least recently played videos beyond the budget; the caller
        deletes the files. Videos still in a queue are kept."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", EVICT_LOCK)
            rows = await conn.fetch(
                """SELECT video_id, size FROM videos v WHERE status = 'ready'
                   AND NOT EXISTS (SELECT 1 FROM queue_items q
                                   WHERE q.video_id = v.video_id)
                   ORDER BY last_used"""
            )
            total = await conn.fetchval(
                "SELECT coalesce(sum(size), 0) FROM videos WHERE status = 'ready'"
            )
            evicted = []
            for r in rows:
                if total <= max_bytes:
                    break
                evicted.append(r["video_id"])
                total -= r["size"]
            # Check again while deleting: a song may have been queued meanwhile.
            rows = await conn.fetch(
                """DELETE FROM videos v WHERE video_id = ANY($1)
                   AND NOT EXISTS (SELECT 1 FROM queue_items q
                                   WHERE q.video_id = v.video_id)
                   RETURNING video_id""",
                evicted,
            )
            return [r["video_id"] for r in rows]
