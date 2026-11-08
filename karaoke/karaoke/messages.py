"""What phones and screens may ask for, validated in one place.

The same messages arrive as JSON over the WebSocket (screens, and phones on
pages older than the htmx one) and as form posts from the phone page. Anything
that doesn't parse is dropped. Free text is trimmed, never rejected.
"""

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from karaoke.library import VIDEO_ID


def _text(limit: int) -> BeforeValidator:
    return BeforeValidator(lambda v: "" if v is None else str(v).strip()[:limit])


Text = Annotated[str, _text(200)]
Name = Annotated[str, _text(40)]
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


class PhotoSkip(Message):
    type: Literal["photo_skip"]


class PhotoRemove(Message):
    type: Literal["photo_remove"]
    photo_id: str
    uploader: Name = ""


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
    | PhotoSkip
    | PhotoRemove
    | Ended
    | Player
    | Position,
    Field(discriminator="type"),
]
_adapter: TypeAdapter[AnyMessage] = TypeAdapter(AnyMessage)


def parse(data: Any) -> Message:
    """Raises pydantic.ValidationError when the message makes no sense."""
    return _adapter.validate_python(data)
