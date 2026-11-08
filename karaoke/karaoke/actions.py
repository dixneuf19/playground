"""What happens when a phone or a screen asks for something.

Runs on any replica. Room changes go through `Store.room_tx` with the logic in
karaoke.rooms; this module only routes each message to it.
"""

import logging
import time

from karaoke.db import RoomTx, Store
from karaoke.library import Library
from karaoke.messages import (
    Add,
    Ended,
    Heart,
    Message,
    Move,
    Pause,
    PhotoRemove,
    PhotoSkip,
    Play,
    Player,
    Position,
    React,
    Remove,
    Restart,
    Retry,
    Skip,
    Volume,
)
from karaoke.photo_files import PhotoFiles
from karaoke.reactions import Reactions
from karaoke.rooms import QueueItem, Room

logger = logging.getLogger(__name__)


class Actions:
    def __init__(self, store: Store, library: Library, photos: PhotoFiles) -> None:
        self.store = store
        self.library = library
        self.photos = photos
        self.reactions = Reactions()

    async def handle(self, code: str, msg: Message, from_screen: bool) -> Room | None:
        """Apply a message to the room. Returns the room as it is afterwards
        (None if it doesn't exist, or for messages that don't touch it)."""
        if msg.screen_only and not from_screen:
            return None
        match msg:
            case Retry():
                await self._retry(code, msg.item_id)
                return None
            case Heart():  # favourites belong to every room, not this one
                await self.heart(msg)
                return None
            case Position():
                await self.store.save_position(code, msg.item_id, msg.t, time.time())
                return None
            case React():
                # Ephemeral: straight to the screens, no lock and no state.
                cmd = self.reactions.command(
                    code, msg.sender, msg.kind, msg.count, time.monotonic()
                )
                if cmd is not None:
                    await self.store.notify_command(code, cmd)
                return None
        async with self.store.room_tx(code) as tx:
            if tx is None:
                return None
            tx.save = await self._apply(tx, msg)
        self.photos.cleanup(tx)
        return tx.room if tx else None

    async def claim_name(self, code: str, token: str, name: str) -> bool | None:
        """The name sheet. False while another phone holds the name; None if
        the room is gone. The phone's queued songs and pending photos follow
        it to the new name, in the same transaction."""
        async with self.store.room_tx(code) as tx:
            if tx is None:
                return None
            previous = await self.store.claim_name(tx, token, name, time.time())
            if previous is None:
                tx.save = False
                return False
            # Saved even when nothing moved: the NOTIFY makes the phone that
            # just lost the name drop it.
            tx.room.rename_guest(previous, name)
        return True

    async def _apply(self, tx: RoomTx, msg: Message) -> bool:
        """Change the locked room; False when nothing changed."""
        room, now = tx.room, time.time()
        match msg:
            case Add():
                return await self._add(tx, msg)
            case Remove():
                return room.remove(msg.item_id)
            case Move():
                return room.move(msg.item_id, msg.index)
            case Skip():
                room.advance()
                return True
            case Play():
                return _command(tx, "play", room.play(now))
            case Pause():
                return _command(tx, "pause", room.pause(now))
            case Restart():
                return _command(tx, "restart", room.restart(now))
            case Volume():
                room.set_volume(msg.value)
                return _command(tx, "volume", True, value=room.volume)
            case PhotoSkip():
                return room.skip_photo(now)
            case PhotoRemove():
                return room.remove_photo(msg.photo_id, msg.uploader)
            case Ended():
                return room.finish(msg.item_id)
            case Player():
                return room.screen_report(msg.state, msg.t, now)
        logger.warning("Unhandled message %r", msg)
        return False

    async def _add(self, tx: RoomTx, msg: Add) -> bool:
        if tx.room.full:
            return False
        # Registered in the same transaction: a refused song leaves no row.
        status = await self.store.video_status(msg.video_id, tx.conn)
        if status == "ready" and not self.library.exists(msg.video_id):
            await self.store.reset_video(msg.video_id, tx.conn)  # evicted or lost
            status = "pending"
        item = QueueItem.new(msg.video_id, msg.title, msg.artist, msg.singer, status)
        tx.room.enqueue(item)
        # The leader downloads queued videos: told once the song is saved.
        tx.wake_downloads = status == "pending"
        return True

    async def heart(self, msg: Heart) -> bool:
        """False when the favourites list is full. No YouTube lookup here: the
        leader checks the phone's title afterwards."""
        return await self.store.set_favourite(
            msg.video_id, msg.on, msg.title, msg.artist
        )

    async def _retry(self, code: str, item_id: str) -> None:
        room = await self.store.load_room(code)
        failed = [i for i in room.queue if i.id == item_id] if room else []
        for item in failed:
            if item.status != "error":
                continue
            await self.store.reset_video(item.video_id)
            await self.store.notify_downloads()
            for room_code in await self.store.rooms_with_video(item.video_id):
                await self.store.notify_room(room_code)


def _command(tx: RoomTx, action: str, changed: bool, **extra: object) -> bool:
    """Tell the room's screens, on whichever replica, if the room changed."""
    if changed:
        tx.command(action, **extra)
    return changed
