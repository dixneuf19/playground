import logging
import mimetypes
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx2

from sfr_mms_bot.parser import MmsNotice

logger = logging.getLogger(__name__)

BASE_URL = "https://www.vosmms.com/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
MEDIA_ATTRS = {
    "img": "src",
    "video": "src",
    "audio": "src",
    "source": "src",
    "embed": "src",
    "object": "data",
    "iframe": "src",
    "frame": "src",
    "a": "href",
}
MEDIA_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".3gp", ".mov", ".amr", ".mp3", ".txt")
MAX_FRAMES = 3


class LoginError(Exception):
    pass


@dataclass
class Part:
    filename: str
    content_type: str
    data: bytes


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = MEDIA_ATTRS.get(tag)
        for name, value in attrs:
            if name == wanted and value:
                self.links.append((tag, value))


def _same_site(url: str) -> bool:
    return urlparse(url).hostname == urlparse(BASE_URL).hostname


def collect_links(html: str, page_url: str) -> tuple[list[str], list[str]]:
    """Split the links of a page into media URLs and frame URLs to follow, both on vosmms.com only."""
    collector = _LinkCollector()
    collector.feed(html)
    media: list[str] = []
    frames: list[str] = []
    for tag, value in collector.links:
        url = urljoin(page_url, value)
        if not _same_site(url):
            continue
        path = urlparse(url).path.lower()
        if tag in ("iframe", "frame"):
            frames.append(url)
        elif tag == "a" and not path.endswith(MEDIA_EXTENSIONS):
            continue
        elif url not in media:
            media.append(url)
    return media, frames


def _filename(url: str, content_type: str, index: int) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    if "." in name:
        return name
    return f"part{index}{mimetypes.guess_extension(content_type) or ''}"


def fetch_mms(notice: MmsNotice, client: httpx2.Client | None = None) -> list[Part]:
    own_client = client is None
    client = client or httpx2.Client(
        base_url=BASE_URL, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=False
    )
    try:
        client.get("/").raise_for_status()
        response = client.post("/loginSubmit.jsp", data={"MSISDN": notice.number, "PWD": notice.password})
        location = response.headers.get("location", "")
        if response.is_redirect and "error=" in location:
            raise LoginError("vosmms.com refused the number or password (an MMS expires after 14 days)")
        while response.is_redirect:
            response = client.get(urljoin(str(response.url), location))
            location = response.headers.get("location", "")
        response.raise_for_status()

        pages = [response]
        media: list[str] = []
        seen_frames: set[str] = set()
        while pages:
            page = pages.pop(0)
            page_media, frames = collect_links(page.text, str(page.url))
            media += [url for url in page_media if url not in media]
            for frame in frames:
                if frame not in seen_frames and len(seen_frames) < MAX_FRAMES:
                    seen_frames.add(frame)
                    pages.append(client.get(frame, follow_redirects=True))
        logger.info("Found %d candidate part(s) behind %d page(s)", len(media), 1 + len(seen_frames))

        parts: list[Part] = []
        for index, url in enumerate(media, 1):
            part = client.get(url, follow_redirects=True)
            if part.status_code != 200 or not part.content:
                continue
            content_type = part.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            if content_type.startswith("text/html"):
                continue
            parts.append(Part(_filename(url, content_type, index), content_type, part.content))
        return parts
    finally:
        if own_client:
            client.close()
