"""What phones and screens may ask for, validated in one place.

The same messages arrive as JSON over the WebSocket (screens, and phones on
pages older than the htmx one) and as form posts from the phone page. Anything
that doesn't parse is dropped. Free text is trimmed, never rejected.
"""

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from karaoke.library import VIDEO_ID
from karaoke.reactions import MAX_BATCH, Kind


def clean_text(value: object, limit: int) -> str:
    """Trimmed to `limit` characters, with no edge spaces left after cutting."""
    return "" if value is None else str(value).strip()[:limit].strip()


def _text(limit: int) -> BeforeValidator:
    return BeforeValidator(lambda v: clean_text(v, limit))


MAX_NAME = 40

Text = Annotated[str, _text(200)]
Name = Annotated[str, _text(MAX_NAME)]
Artist = Annotated[str, _text(80)]
Reason = Annotated[str, _text(140)]
VideoId = Annotated[str, Field(pattern=f"^{VIDEO_ID.pattern}$")]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    # Only the room's screens may send it (they own the player).
    screen_only: ClassVar[bool] = False


class Add(Message):
    type: Literal["add"]
    video_id: VideoId
    title: Text = ""
    artist: Text = ""
    singer: Name = ""


class Remove(Message):
    type: Literal["remove"]
    item_id: str


class Move(Message):
    type: Literal["move"]
    item_id: str
    index: int


class Skip(Message):
    type: Literal["skip"]


class Play(Message):
    type: Literal["play"]


class Pause(Message):
    type: Literal["pause"]


class Restart(Message):
    type: Literal["restart"]


class Volume(Message):
    type: Literal["volume"]
    value: Finite


class Retry(Message):
    type: Literal["retry"]
    item_id: str


class Heart(Message):
    """Add a song to the shared favourites (`on`), or take it out. Title and
    artist come from the phone and are shown at once; the leader replaces
    them with YouTube's (karaoke.favourites)."""

    type: Literal["heart"]
    video_id: VideoId
    on: bool
    title: Text = ""
    artist: Text = ""


class PhotoSkip(Message):
    type: Literal["photo_skip"]


class PhotoRemove(Message):
    type: Literal["photo_remove"]
    photo_id: str
    uploader: Name = ""


class React(Message):
    """Emoji or an effect on the big screen. `count` groups quick taps."""

    type: Literal["react"]
    kind: Kind
    count: Annotated[int, Field(ge=1, le=MAX_BATCH)] = 1
    # Set by the server: the throttle key, the phone's token (the socket for a
    # page without one), never a name.
    sender: Name = ""


class CancelArtist(Message):
    """Global to every room. A missing name or reason is refused later, with
    a message for the guest (karaoke.cancelled.refusal)."""

    type: Literal["cancel_artist"]
    artist: Artist
    reason: Reason = ""
    by: Name = ""


class UncancelArtist(Message):
    type: Literal["uncancel_artist"]
    artist: Artist


class Ended(Message):
    type: Literal["ended"]
    screen_only: ClassVar[bool] = True
    item_id: str


class Player(Message):
    type: Literal["player"]
    screen_only: ClassVar[bool] = True
    state: Literal["playing", "paused"]
    t: Finite | None = None


class Position(Message):
    type: Literal["position"]
    screen_only: ClassVar[bool] = True
    item_id: str
    t: Finite


AnyMessage = Annotated[
    Add
    | Remove
    | Move
    | Skip
    | Play
    | Pause
    | Restart
    | Volume
    | Retry
    | Heart
    | PhotoSkip
    | PhotoRemove
    | React
    | CancelArtist
    | UncancelArtist
    | Ended
    | Player
    | Position,
    Field(discriminator="type"),
]
_adapter: TypeAdapter[AnyMessage] = TypeAdapter(AnyMessage)


def parse(data: Any) -> Message:
    """Raises pydantic.ValidationError when the message makes no sense."""
    return _adapter.validate_python(data)


def from_guest(msg: Message, name: str) -> Message:
    """Who sings, owns a photo or cancels an artist is the name the phone
    holds in the room, never what the message says."""
    if isinstance(msg, Add):
        return msg.model_copy(update={"singer": name})
    if isinstance(msg, PhotoRemove):
        return msg.model_copy(update={"uploader": name})
    if isinstance(msg, CancelArtist):
        return msg.model_copy(update={"by": name})
    return msg
