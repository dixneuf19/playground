import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROUNDS_DIR = Path(__file__).parent / "rounds"


@dataclass(frozen=True)
class Settings:
    # Postgres: a full URL (local dev, tests) or the pieces (Kubernetes secret)
    database_url: str = field(
        default_factory=lambda: os.environ.get("XY_DATABASE_URL", "")
    )
    rounds_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("XY_ROUNDS_DIR", ROUNDS_DIR))
    )

    @property
    def dsn(self) -> str:
        if self.database_url:
            return self.database_url
        env = os.environ
        user = quote(env.get("XY_DB_USER", "xy"), safe="")
        password = quote(env.get("XY_DB_PASSWORD", ""), safe="")
        host = env.get("XY_DB_HOST", "localhost")
        port = env.get("XY_DB_PORT", "5432")
        name = env.get("XY_DB_NAME", "xy")
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
