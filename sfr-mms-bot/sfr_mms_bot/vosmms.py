import html
import logging
import mimetypes
import re
from dataclasses import dataclass, field

import httpx2

from sfr_mms_bot.parser import MmsNotice

logger = logging.getLogger(__name__)

BASE_URL = "https://www.vosmms.com/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
PAGE_ENCODING = "iso-8859-15"

# viewMessage.jsp lists the parts in JS: new Attachement(name, type, isReadable, downloadUrl, displayUrl)
ATTACHMENT_RE = re.compile(r'new\s+Attachement\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"[^"]*"\s*,\s*"([^"]*)"')
# The MMS text is copied in one block per player (image, video, audio, error) or in contentTexte when text-only.
TEXT_RE = re.compile(r'<div\s+(?:id="\w+Texte"\s+class="texte"|class="contentTexte")\s*>(.*?)</div>', re.S | re.I)
TYPE_FALLBACK = {"IMAGE": "image/jpeg", "VIDEO": "video/mp4", "AUDIO": "audio/mpeg", "TEXT": "text/plain"}


class LoginError(Exception):
    pass


@dataclass
class Part:
    filename: str
    content_type: str
    data: bytes


@dataclass
class Mms:
    text: str = ""
    parts: list[Part] = field(default_factory=list)


@dataclass
class Attachment:
    name: str
    kind: str
    url: str

    @property
    def content_type(self) -> str:
        guessed, _ = mimetypes.guess_type(self.name)
        if not guessed:
            return TYPE_FALLBACK.get(self.kind, "application/octet-stream")
        major, subtype = guessed.split("/", 1)
        # mimetypes says audio/3gpp for every .3gp, trust the site's kind for the major type.
        if self.kind in ("IMAGE", "VIDEO", "AUDIO") and major != self.kind.lower():
            return f"{self.kind.lower()}/{subtype}"
        return guessed


def parse_attachments(page: str) -> list[Attachment]:
    return [Attachment(name, kind, html.unescape(url)) for name, kind, url in ATTACHMENT_RE.findall(page)]


def parse_text(page: str) -> str:
    for block in TEXT_RE.findall(page):
        text = re.sub(r"<br\s*/?>", "\n", block, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if text:
            return text
    return ""


def fetch_mms(notice: MmsNotice, client: httpx2.Client | None = None) -> Mms:
    own_client = client is None
    client = client or httpx2.Client(base_url=BASE_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    try:
        client.get("/").raise_for_status()
        login = client.post("/loginSubmit.jsp", data={"MSISDN": notice.number, "PWD": notice.password})
        if not login.is_redirect or "error=" in login.headers.get("location", ""):
            raise LoginError("vosmms.com a refusé le numéro ou le mot de passe (un MMS expire au bout de 14 jours)")
        view = client.get(login.headers["location"], follow_redirects=True)
        view.raise_for_status()
        page = view.content.decode(PAGE_ENCODING, errors="replace")

        mms = Mms(text=parse_text(page))
        attachments = parse_attachments(page)
        logger.info("MMS has %d attachment(s) and %s text", len(attachments), "some" if mms.text else "no")
        for attachment in attachments:
            response = client.get(attachment.url, follow_redirects=True)
            response.raise_for_status()
            mms.parts.append(Part(attachment.name, attachment.content_type, response.content))
        return mms
    finally:
        if own_client:
            client.close()
