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

from karaoke.favourites import (
    MAX_FAILED_CHECKS,
    MAX_FAVOURITES,
    NON_FAVOURITE_SHARE,
    RECHECK_SECONDS,
    Favourite,
)
from karaoke.messages import MAX_NAME
from karaoke.photos import (
    MAX_PENDING_PER_ROOM,
    MAX_PENDING_PER_UPLOADER,
    Photo,
    PhotoQueue,
)
from karaoke.rooms import ItemStatus, QueueItem, Room, generate_code, thumbnail_url
from karaoke.search import split_title

logger = logging.getLogger(__name__)

CHANNEL = "karaoke"
SCHEMA_LOCK = 4242_0001  # pg_advisory_xact_lock keys

# Applied in order at startup, each once (recorded in schema_migrations), under
# an advisory lock. A rolling update runs the previous code against the new
# schema: only add things, or drop what the previous release no longer reads.
MIGRATIONS: list[str] = [
    # 1: the schema as it stood before migrations were numbered; idempotent
    # so that it also runs on databases created by that code.
    """
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
        last_used  timestamptz NOT NULL DEFAULT now()
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
    """,
    # 2: download claims, unused since the leader runs every download.
    """
    ALTER TABLE videos DROP COLUMN IF EXISTS claimed_by;
    ALTER TABLE videos DROP COLUMN IF EXISTS heartbeat;
    """,
    # 3: songs started on a screen, across rooms, for the "popular" list.
    # `title` is the YouTube title (videos.title), never text from a phone.
    """
    CREATE TABLE IF NOT EXISTS plays (
        id        bigserial PRIMARY KEY,
        video_id  text NOT NULL,
        room_code text NOT NULL,
        title     text NOT NULL,
        played_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS plays_played_at ON plays (played_at);
    """,
    # 4: who holds which name in a room: one name per phone, one phone per name.
    """
    CREATE TABLE IF NOT EXISTS guests (
        room_code text NOT NULL REFERENCES rooms(code) ON DELETE CASCADE,
        token     text NOT NULL,
        name      text NOT NULL,
        last_seen timestamptz NOT NULL,
        PRIMARY KEY (room_code, token),
        UNIQUE (room_code, name)
    );
    """,
    # 5: favourites, shared by every room (not tied to a video row: the
    # video may be evicted or never downloaded).
    """
    CREATE TABLE IF NOT EXISTS favourites (
        video_id   text PRIMARY KEY,
        title      text NOT NULL DEFAULT '',
        artist     text NOT NULL DEFAULT '',
        thumbnail  text NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    # 6: favourites keep the phone's title until the leader checks it with
    # YouTube. Rows from before were looked up already: verified.
    """
    ALTER TABLE favourites
        ADD COLUMN IF NOT EXISTS verified boolean NOT NULL DEFAULT true,
        ADD COLUMN IF NOT EXISTS failed_checks int NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS checked_at timestamptz;
    """,
]

# A name whose phone has had no socket open for this long goes to whoever
# asks for it (a new phone, cleared cookies). Sockets refresh it every 30 s.
NAME_RELEASE_SECONDS = 120.0

POPULAR_DAYS = 30
POPULAR_LIMIT = 20


@dataclass
class PopularSong:
    """A song played recently in any room. `plays` counts rooms, not starts."""

    video_id: str
    title: str
    artist: str
    plays: int

    @property
    def thumbnail(self) -> str:
        return thumbnail_url(self.video_id)


@dataclass
class RoomTx:
    """A locked room inside a transaction. Set `save = False` to discard."""

    conn: asyncpg.Connection
    room: Room
    save: bool = True
    commands: list[dict[str, Any]] = field(default_factory=list)
    # Wake the leader on commit: a queued video waits to be downloaded.
    wake_downloads: bool = False
    # Filled on save, for the caller to delete their files: photos that left
    # the queue without being shown (cancelled), and the photo that left the
    # screen (deleted after the screens fade it out).
    removed_photos: list[str] = field(default_factory=list)
    hidden_photo: str | None = None
    # id -> uploader of the photos pending when loaded
    _loaded_pending: dict[str, str] = field(default_factory=dict)
    _loaded_current: str | None = None
    _loaded_photo: str | None = None

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
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version    int PRIMARY KEY,
                       applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            done = {
                r["version"]
                for r in await conn.fetch("SELECT version FROM schema_migrations")
            }
            for version, sql in enumerate(MIGRATIONS, 1):
                if version in done:
                    continue
                logger.info("Applying schema migration %d", version)
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )

    async def ping(self) -> None:
        await self.pool.fetchval("SELECT 1")

    # --- notifications ------------------------------------------------------

    @staticmethod
    async def _notify(conn: asyncpg.Connection, payload: dict[str, Any]) -> None:
        await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, json.dumps(payload))

    async def notify_room(self, code: str) -> None:
        async with self.pool.acquire() as conn:
            await self._notify(conn, {"room": code})

    async def notify_command(self, code: str, command: dict[str, Any]) -> None:
        """A display command outside any room change (reactions)."""
        async with self.pool.acquire() as conn:
            await self._notify(conn, {"room": code, "command": command})

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

    async def load_room(self, code: str) -> Room | None:
        """Read-only, for pushes and pages: one consistent view across the
        room, queue and photo queries."""
        async with (
            self.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            return await self._load(conn, code.upper(), lock=None)

    async def snapshot(self, code: str) -> dict[str, Any] | None:
        room = await self.load_room(code)
        return room.snapshot() if room else None

    async def save_position(
        self, code: str, item_id: str, seconds: float, now: float
    ) -> None:
        """Periodic report from a screen: no lock and no notify, nobody needs
        a push for it. Ignored once the song is no longer the current one.
        Timestamps come from the app's clock, like every other room time."""
        await self.pool.execute(
            """UPDATE rooms SET position = $3, position_at = to_timestamp($4)
               WHERE code = $1 AND current_item_id = $2""",
            code.upper(),
            item_id,
            max(0.0, seconds),
            now,
        )

    @asynccontextmanager
    async def room_tx(self, code: str) -> AsyncGenerator[RoomTx | None]:
        """Yield the locked room (None if missing), save it and notify every
        replica on clean exit."""
        async with self.pool.acquire() as conn, conn.transaction():
            room = await self._load(conn, code.upper(), lock="FOR UPDATE")
            if room is None:
                yield None
                return
            tx = RoomTx(
                conn,
                room,
                _loaded_pending={p.id: p.uploader for p in room.photos.pending},
                _loaded_current=room.current.id if room.current else None,
                _loaded_photo=room.photo.id if room.photo else None,
            )
            yield tx
            if not tx.save:
                return
            await self._save(tx)
            # Delivered on commit, so every replica reads the saved state.
            await self._notify(conn, {"room": room.code})
            for cmd in tx.commands:
                await self._notify(conn, {"room": room.code, "command": cmd})
            if tx.wake_downloads:
                await self._notify(conn, {"downloads": True})

    async def _load(
        self, conn: asyncpg.Connection, code: str, lock: str | None
    ) -> Room | None:
        row = await conn.fetchrow(
            f"""SELECT code, state, volume, sung, photo_id, photo_turn,
                       extract(epoch FROM photo_until)::float8 AS photo_until,
                       position,
                       extract(epoch FROM position_at)::float8 AS position_at
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
            # Only ready videos start, and a download sets their title.
            await conn.execute(
                """INSERT INTO plays (video_id, room_code, title)
                   SELECT video_id, $2, title FROM videos
                   WHERE video_id = $1 AND title <> ''""",
                room.current.video_id,
                room.code,
            )
        # Photos that left the pending queue: shown (now on screen) or cancelled
        remaining = {p.id for p in room.photos.pending}
        for photo_id in tx._loaded_pending.keys() - remaining:
            if room.photo and room.photo.id == photo_id:
                await conn.execute(
                    "UPDATE photos SET state = 'shown', shown_turn = $2 WHERE id = $1",
                    photo_id,
                    room.photos.turn - 1,
                )
            else:
                await conn.execute("DELETE FROM photos WHERE id = $1", photo_id)
                tx.removed_photos.append(photo_id)
        # Photos still pending whose uploader changed name (Room.rename_guest)
        renamed = [
            (p.id, p.uploader)
            for p in room.photos.pending
            if tx._loaded_pending.get(p.id, p.uploader) != p.uploader
        ]
        if renamed:
            await conn.executemany(
                "UPDATE photos SET uploader = $2 WHERE id = $1", renamed
            )
        if tx._loaded_photo and tx._loaded_photo != (room.photo and room.photo.id):
            tx.hidden_photo = tx._loaded_photo

    async def prune_rooms(self, ttl_seconds: int) -> list[str]:
        rows = await self.pool.fetch(
            """DELETE FROM rooms
               WHERE last_activity < now() - make_interval(secs => $1)
               RETURNING code""",
            ttl_seconds,
        )
        return [r["code"] for r in rows]

    # --- plays --------------------------------------------------------------

    async def popular(self) -> list[PopularSong]:
        """The songs sung in the most rooms over the last weeks; ties go to the
        most recent. Counting rooms, not starts, so add + skip in a loop
        doesn't push a song up. Title and artist come from the YouTube title."""
        rows = await self.pool.fetch(
            """SELECT video_id, count(DISTINCT room_code) AS plays,
                      (array_agg(title ORDER BY played_at DESC))[1] AS title
               FROM plays
               WHERE played_at > now() - make_interval(days => $1)
               GROUP BY video_id
               ORDER BY plays DESC, max(played_at) DESC
               LIMIT $2""",
            POPULAR_DAYS,
            POPULAR_LIMIT,
        )
        return [
            PopularSong(r["video_id"], *split_title(r["title"]), r["plays"])
            for r in rows
        ]

    async def prune_plays(self) -> None:
        await self.pool.execute(
            "DELETE FROM plays WHERE played_at < now() - make_interval(days => $1)",
            POPULAR_DAYS,
        )

    async def room_codes(self) -> set[str]:
        return {r["code"] for r in await self.pool.fetch("SELECT code FROM rooms")}

    # --- guests -------------------------------------------------------------

    async def guest_name(self, code: str, token: str) -> str:
        """The name this phone holds in the room, or ""."""
        name = await self.pool.fetchval(
            "SELECT name FROM guests WHERE room_code = $1 AND token = $2",
            code.upper(),
            token,
        )
        return name or ""

    async def guest_names(self, code: str) -> dict[str, str]:
        """token -> name, for every phone holding a name in the room."""
        rows = await self.pool.fetch(
            "SELECT token, name FROM guests WHERE room_code = $1", code.upper()
        )
        return {r["token"]: r["name"] for r in rows}

    @staticmethod
    async def claim_name(tx: RoomTx, token: str, name: str, now: float) -> str | None:
        """Give the name to this phone and return the one it held before (""
        if none); None while another phone holding it was seen recently.

        Runs inside `room_tx`: claims in a room are serialized by the room row
        lock, and the caller moves the old name's songs and photos in the same
        transaction (Room.rename_guest)."""
        conn, code = tx.conn, tx.room.code
        rows = await conn.fetch(
            """SELECT token, name, extract(epoch FROM last_seen)::float8 AS seen
               FROM guests WHERE room_code = $1 AND (name = $2 OR token = $3)""",
            code,
            name,
            token,
        )
        previous = next((r["name"] for r in rows if r["token"] == token), "")
        holder = next((r for r in rows if r["name"] == name), None)
        if holder and holder["token"] != token:
            if holder["seen"] > now - NAME_RELEASE_SECONDS:
                return None
            await conn.execute(
                "DELETE FROM guests WHERE room_code = $1 AND token = $2",
                code,
                holder["token"],
            )
        await conn.execute(
            """INSERT INTO guests (room_code, token, name, last_seen)
               VALUES ($1, $2, $3, to_timestamp($4))
               ON CONFLICT (room_code, token)
               DO UPDATE SET name = $3, last_seen = to_timestamp($4)""",
            code,
            token,
            name,
            now,
        )
        return previous

    async def suggest_name(self, code: str, name: str) -> str:
        """`name 2`, `name 3`…: the first one nobody holds in the room."""
        taken = set((await self.guest_names(code)).values())
        n = 2
        while True:
            suffix = f" {n}"
            candidate = name[: MAX_NAME - len(suffix)].rstrip() + suffix
            if candidate not in taken:
                return candidate
            n += 1

    async def touch_guests(self, phones: list[tuple[str, str]], now: float) -> None:
        """Presence: these (room, token) phones have a socket open right now."""
        if not phones:
            return
        codes, tokens = zip(*phones, strict=True)
        await self.pool.execute(
            """UPDATE guests g SET last_seen = to_timestamp($3)
               FROM unnest($1::text[], $2::text[]) AS p(room_code, token)
               WHERE g.room_code = p.room_code AND g.token = p.token""",
            list(codes),
            list(tokens),
            now,
        )

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

    async def rooms_due_for_photo(self, now: float) -> list[str]:
        rows = await self.pool.fetch(
            """SELECT code FROM rooms r
               WHERE (photo_until IS NULL OR photo_until <= to_timestamp($1))
                 AND (photo_id IS NOT NULL OR EXISTS (
                     SELECT 1 FROM photos p
                     WHERE p.room_code = r.code AND p.state = 'pending'))""",
            now,
        )
        return [r["code"] for r in rows]

    # --- favourites ---------------------------------------------------------

    async def set_favourite(
        self, video_id: str, on: bool, title: str = "", artist: str = ""
    ) -> bool:
        """Heart or unheart a song for every room. False when the list is
        full; hearting a song twice keeps its first title. The title comes
        from the phone: the leader checks it with YouTube later
        (`favourites_to_check`). Every replica pushes the new list to its
        phones. The cap is a soft one: concurrent hearts may each see room
        for one more and go a little past it."""
        async with self.pool.acquire() as conn, conn.transaction():
            if on:
                status = await conn.execute(
                    """INSERT INTO favourites
                           (video_id, title, artist, thumbnail, verified)
                       SELECT $1, $2, $3, $4, false
                       WHERE (SELECT count(*) FROM favourites) < $5
                       ON CONFLICT (video_id) DO NOTHING""",
                    video_id,
                    title,
                    artist,
                    thumbnail_url(video_id),
                    MAX_FAVOURITES,
                )
            else:
                status = await conn.execute(
                    "DELETE FROM favourites WHERE video_id = $1", video_id
                )
            if not status.endswith(" 0"):  # "INSERT 0 1", "DELETE 1"
                await self._notify(conn, {"favourites": True})
            hearted = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM favourites WHERE video_id = $1)",
                video_id,
            )
            return hearted == on

    async def favourites_to_check(self, limit: int) -> list[str]:
        """Favourites still showing the phone's title, oldest first; one whose
        check failed waits a while before the next try."""
        rows = await self.pool.fetch(
            """SELECT video_id FROM favourites
               WHERE NOT verified AND (checked_at IS NULL
                     OR checked_at < now() - make_interval(secs => $2))
               ORDER BY created_at LIMIT $1""",
            limit,
            RECHECK_SECONDS,
        )
        return [r["video_id"] for r in rows]

    async def verify_favourite(self, video_id: str, title: str, artist: str) -> None:
        """YouTube's title replaces the phone's; phones get it if it changed."""
        async with self.pool.acquire() as conn, conn.transaction():
            old = await conn.fetchrow(
                """SELECT title, artist FROM favourites
                   WHERE video_id = $1 AND NOT verified FOR UPDATE""",
                video_id,
            )
            if old is None:  # unhearted meanwhile, or checked already
                return
            await conn.execute(
                """UPDATE favourites SET title = $2, artist = $3, verified = true
                   WHERE video_id = $1""",
                video_id,
                title,
                artist,
            )
            if (old["title"], old["artist"]) != (title, artist):
                await self._notify(conn, {"favourites": True})

    async def favourite_check_failed(self, video_id: str) -> None:
        """YouTube didn't answer for it: try again later, and drop it after a
        few failures (a made-up video id never resolves)."""
        async with self.pool.acquire() as conn, conn.transaction():
            failed = await conn.fetchval(
                """UPDATE favourites
                   SET failed_checks = failed_checks + 1, checked_at = now()
                   WHERE video_id = $1 AND NOT verified
                   RETURNING failed_checks""",
                video_id,
            )
            if failed is not None and failed >= MAX_FAILED_CHECKS:
                await conn.execute(
                    "DELETE FROM favourites WHERE video_id = $1", video_id
                )
                await self._notify(conn, {"favourites": True})

    async def favourites(self) -> list[Favourite]:
        """All of them (storage caps them at MAX_FAVOURITES), most played
        first, counted like `popular` (rooms that sang it over the last
        weeks); ties go to the latest hearted, so a fresh heart is never
        hidden behind played ones."""
        rows = await self.pool.fetch(
            """SELECT f.video_id, f.title, f.artist, f.thumbnail,
                      coalesce(v.status = 'ready', false) AS ready,
                      coalesce(p.plays, 0)::int AS plays
               FROM favourites f
               LEFT JOIN videos v USING (video_id)
               LEFT JOIN (SELECT video_id, count(DISTINCT room_code) AS plays
                          FROM plays
                          WHERE played_at > now() - make_interval(days => $1)
                          GROUP BY video_id) p USING (video_id)
               ORDER BY plays DESC, f.created_at DESC, f.video_id""",
            POPULAR_DAYS,
        )
        return [Favourite(**dict(r)) for r in rows]

    async def hearted(self, video_ids: list[str]) -> set[str]:
        """Which of these videos are favourites."""
        rows = await self.pool.fetch(
            "SELECT video_id FROM favourites WHERE video_id = ANY($1)", video_ids
        )
        return {r["video_id"] for r in rows}

    # --- videos -------------------------------------------------------------

    async def video_status(
        self, video_id: str, conn: asyncpg.Connection | None = None
    ) -> ItemStatus:
        """Register the video if new (as pending) and return its status."""
        db = conn or self.pool
        return await db.fetchval(
            """INSERT INTO videos (video_id) VALUES ($1)
               ON CONFLICT (video_id) DO UPDATE SET video_id = EXCLUDED.video_id
               RETURNING status""",
            video_id,
        )

    async def reset_video(
        self, video_id: str, conn: asyncpg.Connection | None = None
    ) -> None:
        """Download again: after an error, or when the file went missing."""
        await (conn or self.pool).execute(
            """UPDATE videos SET status = 'pending', progress = 0
               WHERE video_id = $1 AND status IN ('ready', 'error')""",
            video_id,
        )

    # Downloads are run by the leader replica only (karaoke.leader), so a
    # plain status is enough: no claims between replicas.

    async def start_download(self, video_id: str) -> bool:
        started = await self.pool.fetchval(
            """UPDATE videos SET status = 'downloading', progress = 0
               WHERE video_id = $1 AND status = 'pending' RETURNING video_id""",
            video_id,
        )
        return started is not None

    async def set_progress(self, video_id: str, progress: float) -> None:
        await self.pool.execute(
            "UPDATE videos SET progress = $2 WHERE video_id = $1 AND status = 'downloading'",
            video_id,
            progress,
        )

    async def finish_download(
        self, video_id: str, ok: bool, title: str = "", size: int = 0
    ) -> None:
        await self.pool.execute(
            """UPDATE videos SET status = $2, progress = $3, title = $4, size = $5,
                   last_used = now()
               WHERE video_id = $1""",
            video_id,
            "ready" if ok else "error",
            1.0 if ok else 0.0,
            title,
            size,
        )

    async def requeue_interrupted_downloads(self) -> None:
        """A new leader starts over what the previous one left unfinished."""
        await self.pool.execute(
            "UPDATE videos SET status = 'pending', progress = 0 WHERE status = 'downloading'"
        )

    async def notify_downloads(self) -> None:
        """Wake the leader: a video waits to be downloaded."""
        async with self.pool.acquire() as conn:
            await self._notify(conn, {"downloads": True})

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
        leader died between finishing a download and starting the song)."""
        rows = await self.pool.fetch(
            """SELECT DISTINCT q.room_code FROM queue_items q
               JOIN videos v USING (video_id)
               WHERE v.status = 'ready' AND NOT q.is_current
                 AND NOT EXISTS (SELECT 1 FROM queue_items c
                                 WHERE c.room_code = q.room_code AND c.is_current)"""
        )
        return [r["room_code"] for r in rows]

    async def pending_downloads(self) -> list[str]:
        """Queued videos waiting to be downloaded, oldest request first."""
        rows = await self.pool.fetch(
            """SELECT v.video_id FROM videos v JOIN queue_items q USING (video_id)
               WHERE v.status = 'pending'
               GROUP BY v.video_id ORDER BY min(q.position)"""
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
        deletes the files. Videos still in a queue are kept. Other videos go
        once the cache is past a share of the budget, favourites only past
        all of it: the rest of the budget is theirs."""
        others_bytes = int(max_bytes * NON_FAVOURITE_SHARE)
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """SELECT video_id, size,
                          EXISTS (SELECT 1 FROM favourites f
                                  WHERE f.video_id = v.video_id) AS favourite
                   FROM videos v WHERE status = 'ready'
                   AND NOT EXISTS (SELECT 1 FROM queue_items q
                                   WHERE q.video_id = v.video_id)
                   ORDER BY favourite, last_used"""
            )
            total = await conn.fetchval(
                "SELECT coalesce(sum(size), 0) FROM videos WHERE status = 'ready'"
            )
            evicted = []
            for r in rows:
                if total <= (max_bytes if r["favourite"] else others_bytes):
                    continue
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
