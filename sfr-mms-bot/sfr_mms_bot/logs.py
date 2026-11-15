import logging
import sys
from types import TracebackType

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__(FORMAT)
        self.secrets = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        return text


def setup(secrets: list[str]) -> None:
    """Log to stderr with secrets removed, uncaught exceptions included."""
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(secrets))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Their INFO logs print every request URL, and Telegram's carry the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    def excepthook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        logging.getLogger("sfr_mms_bot").critical("Uncaught exception", exc_info=(kind, value, tb))

    sys.excepthook = excepthook
