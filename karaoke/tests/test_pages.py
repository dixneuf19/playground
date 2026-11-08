"""The htmx phone page: server-rendered HTML, actions over HTTP, fragments
pushed over the WebSocket."""

import json
from urllib.parse import quote

from fastapi.testclient import TestClient

from karaoke.search import SearchResult
from tests.conftest import services

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
VID = "abcdefghijk"


def _room(client: TestClient) -> str:
    return client.post("/api/rooms", json={"password": "secret"}).json()["code"]


def _as(client: TestClient, name: str) -> TestClient:
    client.cookies.set("karaoke_name", quote(name))  # as guest.js does
    return client


def test_page_asks_for_a_name_until_it_has_one(client: TestClient) -> None:
    code = _room(client)
    assert 'class="sheet" id="name-sheet"' in client.get(f"/r/{code}").text
    page = _as(client, "Léa").get(f"/r/{code}").text
    assert 'class="sheet hidden" id="name-sheet"' in page
    assert "🎤 <span>Léa</span>" in page
    assert f'ws-connect="/ws/{code}?role=guest&view=html"' in page
    assert page.count('id="everywhere"') == 1


def test_add_takes_the_singer_from_the_cookie(client: TestClient) -> None:
    code = _room(client)
    res = _as(client, "Léa").post(
        f"/r/{code}/do",
        data={"type": "add", "video_id": VID, "title": "Song", "singer": "Liar"},
    )
    assert res.status_code == 204
    toast = json.loads(res.headers["HX-Trigger"])["toast"]
    assert toast == "Ajouté, c'est bientôt à toi 🎤"  # first in line
    room = client.get(f"/api/rooms/{code}").json()
    assert [(i["title"], i["singer"]) for i in room["queue"]] == [("Song", "Léa")]

    assert client.post(f"/r/{code}/do", data={"type": "add"}).status_code == 422
    assert client.post("/r/ZZZZ/do", data={"type": "skip"}).status_code == 404


def test_pushed_fragments_are_personal_and_escaped(client: TestClient) -> None:
    code = _room(client)
    _as(client, "Léa")
    with client.websocket_connect(f"/ws/{code}?view=html") as lea:
        first = lea.receive_text()
        assert 'id="queue-panel" hx-swap-oob="morph"' in first
        client.post(
            f"/r/{code}/do",
            data={"type": "add", "video_id": VID, "title": "<script>x</script>"},
        )
        pushed = lea.receive_text()
    assert "&lt;script&gt;" in pushed and "<script>" not in pushed
    assert "tag-me" in pushed  # her own song
    _as(client, "Tom")
    assert "tag-me" not in client.get(f"/r/{code}/state").text


def test_only_the_uploader_sees_their_waiting_photos(client: TestClient) -> None:
    code = _room(client)
    url = f"/api/rooms/{code}/photos"
    _as(client, "Léa")
    client.post(url, content=JPEG)  # goes on screen
    waiting = client.post(url, content=JPEG).json()["id"]
    assert f'id="ph-{waiting}"' in client.get(f"/r/{code}/state").text
    _as(client, "Tom")
    assert waiting not in client.get(f"/r/{code}/state").text
    # Tom can't cancel it: the uploader is the cookie, not the form
    client.post(
        f"/r/{code}/do",
        data={"type": "photo_remove", "photo_id": waiting, "uploader": "Léa"},
    )
    _as(client, "Léa")
    assert f'id="ph-{waiting}"' in client.get(f"/r/{code}/state").text
    client.post(f"/r/{code}/do", data={"type": "photo_remove", "photo_id": waiting})
    assert waiting not in client.get(f"/r/{code}/state").text


def test_search_renders_results(client: TestClient) -> None:
    async def fake(_q: str, everywhere: bool, _link: str | None) -> list[SearchResult]:
        return [
            SearchResult(
                video_id=VID,
                title="Les lacs du Connemara",
                artist="Michel Sardou",
                raw_title="",
                channel="KaraFun",
                duration=245,
                thumbnail="t",
                curated=not everywhere,
            )
        ]

    setattr(services(client).searcher, "_search", fake)  # noqa: B010
    code = _room(client)
    html = _as(client, "Léa").get(f"/r/{code}/search", params={"q": "connemara"}).text
    assert "Catalogue karaoké" in html
    assert "4:05" in html
    assert 'data-confirm-yes="J\'assume"' in html  # cancelled: asks first
    assert 'name="everywhere" value="0"' in html

    client.post(f"/r/{code}/do", data={"type": "add", "video_id": VID})
    html = client.get(
        f"/r/{code}/search", params={"q": "connemara", "everywhere": 1}
    ).text
    assert "Résultats YouTube" in html
    assert 'name="everywhere" value="1"' in html
    assert "✓" in html  # already queued by Léa
    assert client.get("/r/ZZZZ/search", params={"q": "x"}).status_code == 404
