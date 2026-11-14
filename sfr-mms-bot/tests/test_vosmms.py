import httpx2
import pytest

from sfr_mms_bot.parser import MmsNotice
from sfr_mms_bot.vosmms import LoginError, collect_links, fetch_mms

NOTICE = MmsNotice(sender="0611111111", number="0622222222", password="p@ss.w0rd")
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def test_collect_links_keeps_vosmms_media_only() -> None:
    html = """
    <img src="/mms/part1.jpg"><img src="https://s1.s-sfr.fr/logo.png">
    <a href="part2.txt">texte</a><a href="/help.jsp">aide</a>
    <video src="/mms/v.3gp"></video><iframe src="/frame.jsp"></iframe>
    """
    media, frames = collect_links(html, "https://www.vosmms.com/view.jsp")
    assert media == [
        "https://www.vosmms.com/mms/part1.jpg",
        "https://www.vosmms.com/part2.txt",
        "https://www.vosmms.com/mms/v.3gp",
    ]
    assert frames == ["https://www.vosmms.com/frame.jsp"]


def make_client(handler) -> httpx2.Client:
    return httpx2.Client(base_url="https://www.vosmms.com/", transport=httpx2.MockTransport(handler))


def test_login_refused() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/loginSubmit.jsp":
            return httpx2.Response(302, headers={"location": "https://www.vosmms.com/login.jsp?error=true"})
        return httpx2.Response(200, text="<html></html>")

    with pytest.raises(LoginError):
        fetch_mms(NOTICE, make_client(handler))


def test_fetches_parts_after_login() -> None:
    posted: dict[str, str] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        match request.url.path:
            case "/loginSubmit.jsp":
                posted.update(httpx2.QueryParams(request.content.decode()))
                return httpx2.Response(302, headers={"location": "/view.jsp"})
            case "/view.jsp":
                return httpx2.Response(200, text='<img src="/p/1.jpg"><iframe src="/frame.jsp"></iframe>')
            case "/frame.jsp":
                return httpx2.Response(200, text='<a href="/p/2.txt">x</a>')
            case "/p/1.jpg":
                return httpx2.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
            case "/p/2.txt":
                return httpx2.Response(200, text="Coucou", headers={"content-type": "text/plain; charset=utf-8"})
        return httpx2.Response(200, text="<html></html>")

    parts = fetch_mms(NOTICE, make_client(handler))
    assert posted == {"MSISDN": "0622222222", "PWD": "p@ss.w0rd"}
    assert [(p.filename, p.content_type, p.data) for p in parts] == [
        ("1.jpg", "image/jpeg", JPEG),
        ("2.txt", "text/plain", b"Coucou"),
    ]
