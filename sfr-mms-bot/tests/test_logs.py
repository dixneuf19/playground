import logging

from sfr_mms_bot.logs import RedactingFormatter

TOKEN = "123456:FAKEtokenFAKEtoken"


def record(msg: str, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, __file__, 1, msg, None, exc_info)


def test_redacts_message() -> None:
    text = RedactingFormatter([TOKEN]).format(record(f"GET https://api.telegram.org/bot{TOKEN}/getMe"))
    assert TOKEN not in text
    assert "bot<redacted>/getMe" in text


def test_redacts_traceback() -> None:
    try:
        raise RuntimeError(f"The token `{TOKEN}` was rejected by the server.")
    except RuntimeError as e:
        text = RedactingFormatter([TOKEN]).format(record("boom", (type(e), e, e.__traceback__)))
    assert "was rejected" in text
    assert TOKEN not in text
