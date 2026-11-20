"""Settings, read from the environment (COCO_*)."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"COCO_{name}", default)


@dataclass(frozen=True)
class Settings:
    # Where phones reach the app: the QR codes point there.
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL").rstrip("/"))
    # The LiveKit server, as browsers reach it (wss://...).
    livekit_url: str = field(
        default_factory=lambda: _env("LIVEKIT_URL", "ws://localhost:7880")
    )
    livekit_api_key: str = field(
        default_factory=lambda: _env("LIVEKIT_API_KEY", "devkey")
    )
    livekit_api_secret: str = field(
        default_factory=lambda: _env("LIVEKIT_API_SECRET", "secret")
    )
    livekit_room: str = field(default_factory=lambda: _env("ROOM", "coco-lanta"))
    # Empty: anyone opening /regie runs the show.
    regie_password: str = field(default_factory=lambda: _env("REGIE_PASSWORD"))


settings = Settings()
