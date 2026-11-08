"""Store behaviour that matters with several replicas."""

import asyncio
from collections.abc import Callable

from karaoke.config import Settings
from karaoke.db import MIGRATIONS, Store
from karaoke.rooms import QueueItem

VID = "abcdefghijk"


def _run(make_settings: Callable[..., Settings], test) -> None:
    async def main() -> None:
        store = await Store.connect(make_settings().dsn())
        try:
            await test(store)
        finally:
            await store.close()

    asyncio.run(main())


async def _queue(store: Store, video_id: str) -> str:
    code = await store.create_room()
    async with store.room_tx(code) as tx:
        assert tx is not None
        tx.room.add(
            QueueItem(video_id=video_id, title="t", artist="", singer="s", thumbnail="")
        )
    return code


def test_download_runs_once_and_restarts_after_a_leader_change(
    make_settings: Callable[..., Settings],
) -> None:
    async def test(store: Store) -> None:
        await _queue(store, VID)
        await store.video_status(VID)
        assert await store.pending_downloads() == [VID]
        assert await store.start_download(VID)
        assert not await store.start_download(VID)  # already running
        assert await store.pending_downloads() == []
        # The leader died mid-download: the next one starts it over
        await store.requeue_interrupted_downloads()
        assert await store.pending_downloads() == [VID]

    _run(make_settings, test)


def test_eviction_keeps_queued_videos(make_settings: Callable[..., Settings]) -> None:
    async def test(store: Store) -> None:
        for vid in (VID, "kkkkkkkkkkk"):
            await store.video_status(vid)
            await store.start_download(vid)
            await store.finish_download(vid, ok=True, size=100)
        await _queue(store, VID)
        assert await store.evict(max_bytes=0) == ["kkkkkkkkkkk"]
        assert await store.cached([VID]) == {VID}

    _run(make_settings, test)


def test_lost_video_rows_are_restored(make_settings: Callable[..., Settings]) -> None:
    async def test(store: Store) -> None:
        await _queue(store, VID)  # queued without any video row
        await store.restore_queued_videos()
        assert await store.pending_downloads() == [VID]

    _run(make_settings, test)


def test_migrations_run_once(make_settings: Callable[..., Settings]) -> None:
    async def test(store: Store) -> None:
        await store.migrate()  # a second replica starting: nothing to do
        versions = [
            r["version"]
            for r in await store.pool.fetch("SELECT version FROM schema_migrations")
        ]
        assert versions == list(range(1, len(MIGRATIONS) + 1))

    _run(make_settings, test)


def test_migrations_upgrade_a_database_from_before_they_were_numbered(
    make_settings: Callable[..., Settings],
) -> None:
    async def test(store: Store) -> None:
        # What the previous release created: no migrations table, old columns.
        await store.pool.execute(
            "DROP TABLE schema_migrations; "
            "ALTER TABLE videos ADD COLUMN claimed_by text, ADD COLUMN heartbeat timestamptz"
        )
        await store.migrate()
        columns = {
            r["column_name"]
            for r in await store.pool.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'videos'"
            )
        }
        assert "claimed_by" not in columns

    _run(make_settings, test)


async def _sing(store: Store, code: str, video_id: str, title: str) -> None:
    async with store.room_tx(code) as tx:
        assert tx is not None
        tx.room.add(
            QueueItem(
                video_id=video_id,
                title=title,
                artist="a",
                singer="s",
                thumbnail="",
                status="ready",
            )
        )
        tx.room.advance()


def test_popular_songs_count_plays_across_rooms(
    make_settings: Callable[..., Settings],
) -> None:
    async def test(store: Store) -> None:
        first, second = await store.create_room(), await store.create_room()
        await _sing(store, first, VID, "old title")
        await _sing(store, second, VID, "Connemara")
        await _sing(store, first, "bbbbbbbbbbb", "B")
        await _sing(store, second, "ccccccccccc", "C")
        # A save that doesn't start a new song is not a play
        async with store.room_tx(first) as tx:
            assert tx is not None
            tx.room.set_volume(0.5)
        popular = await store.popular()
        assert [(p.video_id, p.plays) for p in popular] == [
            (VID, 2),
            ("ccccccccccc", 1),  # ties: most recent first
            ("bbbbbbbbbbb", 1),
        ]
        assert popular[0].title == "Connemara"  # from the latest play

        await store.pool.execute(
            "UPDATE plays SET played_at = now() - interval '31 days' "
            "WHERE video_id <> 'ccccccccccc'"
        )
        assert [p.video_id for p in await store.popular()] == ["ccccccccccc"]
        await store.prune_plays()
        assert await store.pool.fetchval("SELECT count(*) FROM plays") == 1

    _run(make_settings, test)
