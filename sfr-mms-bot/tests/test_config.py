import pytest

from sfr_mms_bot.config import ConfigError, parse_allowed_users


def test_parses_ids() -> None:
    assert parse_allowed_users("1, 22,333") == {1, 22, 333}


@pytest.mark.parametrize("raw", ["", " ", ",", " , "])
def test_empty_list_fails_closed(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_allowed_users(raw)


def test_garbage_fails() -> None:
    with pytest.raises(ConfigError):
        parse_allowed_users("julen")
