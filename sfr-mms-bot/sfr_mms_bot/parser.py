import re
from dataclasses import dataclass

SMS_RE = re.compile(
    r"(?P<sender>\+?[\d .]{9,})\s+vous\s+a\s+envoy[ée]\s+un\s+MMS"
    r".*?avec\s+votre\s+num[ée]ro\s+(?P<number>\+?[\d .]{9,}?)"
    r"\s+et\s+le\s+mot\s+de\s+passe\s+(?P<password>\S+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MmsNotice:
    sender: str
    number: str
    password: str

    def __repr__(self) -> str:
        return f"MmsNotice(sender={self.sender!r}, number={self.number!r}, password=***)"


class ParseError(ValueError):
    pass


def normalize_number(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+33"):
        digits = "0" + digits[3:]
    elif digits.startswith("0033"):
        digits = "0" + digits[4:]
    if not re.fullmatch(r"0\d{9}", digits):
        raise ParseError(f"not a French phone number: {raw!r}")
    return digits


def parse_sms(text: str) -> MmsNotice:
    match = SMS_RE.search(text)
    if not match:
        raise ParseError("not an SFR MMS notice")
    password = match.group("password")
    # The sentence ends right after the password, so its last "." is punctuation.
    if password.endswith("."):
        password = password[:-1]
    if not password:
        raise ParseError("empty password")
    return MmsNotice(
        sender=normalize_number(match.group("sender")),
        number=normalize_number(match.group("number")),
        password=password,
    )
