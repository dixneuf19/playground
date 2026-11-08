import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote


def _channels() -> list[str]:
    raw = os.environ.get("KARAOKE_CHANNELS", "karafunfr,karafun")
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("KARAOKE_DATA_DIR", "./data"))
    )
    # Empty means anyone can create a room. Guests never need it.
    host_password: str = field(
        default_factory=lambda: os.environ.get("KARAOKE_HOST_PASSWORD", "")
    )
    # Public base URL encoded in the QR code. Empty: derived from the request.
    public_url: str = field(
        default_factory=lambda: os.environ.get("KARAOKE_PUBLIC_URL", "").rstrip("/")
    )
    # Curated YouTube channels searched by default, in priority order.
    channels: list[str] = field(default_factory=_channels)
    # The video cache never grows past this size (see db.Store.evict).
    cache_max_gb: float = field(
        default_factory=lambda: float(os.environ.get("KARAOKE_CACHE_MAX_GB", "20"))
    )
    max_height: int = field(
        default_factory=lambda: int(os.environ.get("KARAOKE_MAX_HEIGHT", "720"))
    )

    # Postgres: a full URL (local dev, tests) or the pieces (Kubernetes secret)
    database_url: str = field(
        default_factory=lambda: os.environ.get("KARAOKE_DATABASE_URL", "")
    )

    @property
    def songs_dir(self) -> Path:
        return self.data_dir / "songs"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    def dsn(self) -> str:
        if self.database_url:
            return self.database_url
        env = os.environ
        user = quote(env.get("KARAOKE_DB_USER", "karaoke"), safe="")
        password = quote(env.get("KARAOKE_DB_PASSWORD", ""), safe="")
        host = env.get("KARAOKE_DB_HOST", "localhost")
        port = env.get("KARAOKE_DB_PORT", "5432")
        name = env.get("KARAOKE_DB_NAME", "karaoke")
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"


settings = Settings()
