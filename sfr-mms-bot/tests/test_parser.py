import pytest

from sfr_mms_bot.parser import MmsNotice, ParseError, normalize_number, parse_sms


def sms(password: str, sender: str = "0611111111", number: str = "0622222222") -> str:
    return (
        f"Info SFR : {sender} vous a envoyé un MMS consultable sur www.vosmms.com avec votre numéro "
        f"{number} et le mot de passe {password}. Merci de votre confiance."
    )


@pytest.mark.parametrize(
    "password",
    [
        ")AbC1@xY9",
        "abc.def",
        "a.b.c.",
        "x.",
        "..",
        "!$%&*(+=",
        "pass:word;",
        "Mo,t@P4s.",
        "é#ç~^",
        "1",
    ],
)
def test_password_keeps_everything_but_the_final_period(password: str) -> None:
    assert parse_sms(sms(password)).password == password


def test_numbers_login_is_mine_not_the_sender() -> None:
    notice = parse_sms(sms(")AbC1@xY9"))
    assert notice == MmsNotice(sender="0611111111", number="0622222222", password=")AbC1@xY9")


def test_without_merci() -> None:
    text = "Info SFR : 0611111111 vous a envoyé un MMS consultable sur www.vosmms.com avec votre numéro 0622222222 et le mot de passe a.b@c."
    assert parse_sms(text).password == "a.b@c"


def test_without_final_period() -> None:
    text = "Info SFR : 0611111111 vous a envoyé un MMS consultable sur www.vosmms.com avec votre numéro 0622222222 et le mot de passe a.b@c"
    assert parse_sms(text).password == "a.b@c"


def test_surrounding_text_and_odd_whitespace() -> None:
    text = "Fwd:\n" + sms("p@ss.w0rd").replace(" ", " ") + "\n\nsent from my phone"
    notice = parse_sms(text)
    assert notice.password == "p@ss.w0rd"
    assert notice.number == "0622222222"


def test_accents_stripped() -> None:
    text = sms("Xy9)").replace("é", "e")
    assert parse_sms(text).password == "Xy9)"


def test_international_and_spaced_numbers() -> None:
    notice = parse_sms(sms("pw", sender="+33 6 11 11 11 11", number="06 22 22 22 22"))
    assert notice.sender == "0611111111"
    assert notice.number == "0622222222"


@pytest.mark.parametrize("text", ["", "hello", "Info SFR : votre facture est disponible."])
def test_not_a_notice(text: str) -> None:
    with pytest.raises(ParseError):
        parse_sms(text)


def test_bad_number() -> None:
    with pytest.raises(ParseError):
        normalize_number("12345")


def test_repr_hides_password() -> None:
    assert ")AbC1@xY9" not in repr(parse_sms(sms(")AbC1@xY9")))
