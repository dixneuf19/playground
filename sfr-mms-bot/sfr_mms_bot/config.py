import os

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def parse_allowed_users(raw: str) -> frozenset[int]:
    try:
        users = frozenset(int(uid) for uid in raw.replace(" ", "").split(",") if uid)
    except ValueError as e:
        raise ConfigError(f"TELEGRAM_ALLOWED_USERS must be comma-separated user IDs: {e}") from e
    if not users:
        raise ConfigError("TELEGRAM_ALLOWED_USERS is empty, refusing to start a bot anyone could use")
    return users


def load() -> tuple[str, frozenset[int]]:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is not set")
    return token, parse_allowed_users(os.getenv("TELEGRAM_ALLOWED_USERS", ""))
