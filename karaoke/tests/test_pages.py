"""The htmx phone page: server-rendered HTML, actions over HTTP, fragments
pushed over the WebSocket."""

import json
import re
from urllib.parse import quote

from fastapi.testclient import TestClient

from karaoke.search import SearchResult
from tests.conftest import services

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
VID = "abcdefghijk"


def _room(client: TestClient) -> str:
    return client.post("/api/rooms", json={"password": "secret"}).json()["code"]


def _as(client: TestClient, code: str, name: str) -> TestClient:
    client.cookies.set(f"karaoke_name_{code}", quote(name))  # as guest.js does
    return client


def test_page_asks_for_a_name_until_it_has_one(client: TestClient) -> None:
    code = _room(client)
    assert 'class="sheet" id="name-sheet"' in client.get(f"/r/{code}").text
    page = _as(client, code, "Léa").get(f"/r/{code}").text
    assert 'class="sheet hidden" id="name-sheet"' in page
    assert "🎤 <span>Léa</span>" in page
    assert f'hx-ws:connect="/ws/{code}?role=guest&view=html"' in page
    assert page.count('id="everywhere"') == 1


def test_the_name_is_per_room_and_the_last_one_only_suggested(
    client: TestClient,
) -> None:
    first, second = _room(client), _room(client)
    _as(client, first, "Léa")
    client.cookies.set("karaoke_name", quote("Léa"))  # last name used
    assert "🎤 <span>Léa</span>" in client.get(f"/r/{first}").text
    page = client.get(f"/r/{second}").text
    # Not confirmed for this room: the sheet opens, prefilled, to confirm
    assert 'class="sheet" id="name-sheet"' in page
    assert 'value="Léa"' in page
    assert "C&#39;est moi" in page or "C'est moi" in page
    assert "🎤 <span>?</span>" in page
    # and nothing is attributed to Léa in that room until she confirms
    client.post(f"/r/{second}/do", data={"type": "add", "video_id": VID})
    room = client.get(f"/api/rooms/{second}").json()
    assert room["queue"][0]["singer"] == "Anonyme"


def test_photos_belong_to_the_name_confirmed_in_that_room(
    client: TestClient,
) -> None:
    first, second = _room(client), _room(client)
    _as(client, first, "Léa")
    client.cookies.set("karaoke_name", quote("Léa"))  # last name used
    # The room cookie beats a forged ?uploader=
    client.post(f"/api/rooms/{first}/photos?uploader=Tom", content=JPEG)
    waiting = client.post(
        f"/api/rooms/{first}/photos?uploader=Tom", content=JPEG
    ).json()["id"]
    # Same room through a lowercase URL: still Léa's
    assert f'id="ph-{waiting}"' in client.get(f"/r/{first.lower()}/state").text
    # Not confirmed in the second room: neither cookie gives her rights there
    client.post(f"/api/rooms/{second}/photos", content=JPEG)
    theirs = client.post(f"/api/rooms/{second}/photos", content=JPEG).json()["id"]
    assert theirs not in client.get(f"/r/{second}/state").text
    client.post(f"/r/{second}/do", data={"type": "photo_remove", "photo_id": theirs})
    _as(client, second, "Anonyme")
    assert f'id="ph-{theirs}"' in client.get(f"/r/{second}/state").text


def test_add_takes_the_singer_from_the_cookie(client: TestClient) -> None:
    code = _room(client)
    res = _as(client, code, "Léa").post(
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


def test_reactions_from_the_phone_page(client: TestClient) -> None:
    code = _room(client)
    page = _as(client, code, "Léa").get(f"/r/{code}").text
    assert 'data-react="fire"' in page and 'data-react="confetti"' in page
    with client.websocket_connect(f"/ws/{code}?role=display") as screen:
        screen.receive_json()
        do = f"/r/{code}/do"
        react = {"type": "react", "kind": "laugh", "count": 2}
        assert client.post(do, data=react).status_code == 204
        react = {"type": "react", "kind": "hearts"}
        assert client.post(do, data=react).status_code == 204
        assert screen.receive_json() == {
            "type": "command",
            "action": "react",
            "emoji": "😂",
            "count": 2,
        }
        assert screen.receive_json() == {
            "type": "command",
            "action": "react",
            "effect": "hearts",
        }
    res = client.post(f"/r/{code}/do", data={"type": "react", "kind": "nope"})
    assert res.status_code == 422


def test_pushed_fragments_are_personal_and_escaped(client: TestClient) -> None:
    code = _room(client)
    _as(client, code, "Léa")
    with client.websocket_connect(f"/ws/{code}?view=html") as lea:
        first = lea.receive_text()
        assert 'id="queue-panel" hx-swap-oob="outerMorph"' in first
        client.post(
            f"/r/{code}/do",
            data={"type": "add", "video_id": VID, "title": "<script>x</script>"},
        )
        pushed = lea.receive_text()
    assert "&lt;script&gt;" in pushed and "<script>" not in pushed
    assert "tag-me" in pushed  # her own song
    _as(client, code, "Tom")
    assert "tag-me" not in client.get(f"/r/{code}/state").text


def test_only_the_uploader_sees_their_waiting_photos(client: TestClient) -> None:
    code = _room(client)
    url = f"/api/rooms/{code}/photos"
    _as(client, code, "Léa")
    client.post(url, content=JPEG)  # goes on screen
    waiting = client.post(url, content=JPEG).json()["id"]
    assert f'id="ph-{waiting}"' in client.get(f"/r/{code}/state").text
    _as(client, code, "Tom")
    assert waiting not in client.get(f"/r/{code}/state").text
    # Tom can't cancel it: the uploader is the cookie, not the form
    client.post(
        f"/r/{code}/do",
        data={"type": "photo_remove", "photo_id": waiting, "uploader": "Léa"},
    )
    _as(client, code, "Léa")
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
    html = (
        _as(client, code, "Léa")
        .get(f"/r/{code}/search", params={"q": "connemara"})
        .text
    )
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


def _tag(page: str, element_id: str) -> tuple[str, set[str]]:
    """The opening tag of the element with that id, and its classes."""
    tag = re.search(rf'<(\w+)[^>]*\bid="{element_id}"[^>]*>', page)
    assert tag, element_id
    classes = re.search(r'\bclass="([^"]*)"', tag.group(0))
    return tag.group(0), set(classes.group(1).split()) if classes else set()


def test_the_queue_tab_holds_the_player(client: TestClient) -> None:
    code = _room(client)
    page = _as(client, code, "Léa").get(f"/r/{code}").text
    assert 'data-view="stage"' not in page and 'id="stage"' not in page
    assert "hidden" in _tag(page, "player")[1]  # empty room: nothing to show

    # A song still downloading: the player says one is coming
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": "bcdefghijkl"})
    page = client.get(f"/r/{code}").text
    assert "hidden" not in _tag(page, "player")[1]
    assert "la prochaine chanson se prépare" in page

    svc = services(client)
    path = svc.library.path_for(VID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")
    portal = client.portal
    assert portal is not None
    portal.call(svc.store.video_status, VID)  # registers the video
    portal.call(svc.store.finish_download, VID, True, "Song", 1)
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": VID, "title": "Song"})
    page = client.get(f"/r/{code}").text
    queue_tab = page[page.index('id="view-queue"') : page.index('id="view-photos"')]
    player = queue_tab[
        queue_tab.index('id="player"') : queue_tab.index('id="queue-panel"')
    ]
    for control in ("Recommencer", "Pause", "Suivante", 'id="volume"'):
        assert control in player
    assert "se prépare" not in player
    # The search tab's mini card is a button leading to it
    tag, classes = _tag(page, "now-mini")
    assert tag.startswith("<button") and 'data-view="queue"' in tag
    assert "hidden" not in classes


def test_search_intro_shows_popular_songs(client: TestClient) -> None:
    svc = services(client)
    path = svc.library.path_for(VID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")
    portal = client.portal
    assert portal is not None
    portal.call(svc.store.video_status, VID)
    portal.call(
        svc.store.finish_download, VID, True, "Les lacs du Connemara - Michel Sardou", 1
    )
    code = _room(client)
    _as(client, code, "Léa")
    assert "Les plus chantées" not in client.get(f"/r/{code}").text

    # Ready, so it starts at once: that's a play
    add = {"type": "add", "video_id": VID, "title": "Phone text", "artist": "x"}
    client.post(f"/r/{code}/do", data=add)
    # Other rooms see the YouTube title, never what a phone sent
    assert "Phone text" not in client.get(f"/r/{code}/search").text
    for html in (client.get(f"/r/{code}").text, client.get(f"/r/{code}/search").text):
        assert "Les plus chantées" in html
        assert "Les lacs du Connemara" in html
        assert "🎤 1 fois" in html
        assert "Prêt" in html
        assert 'data-confirm-yes="J\'assume"' in html  # cancelled: asks first
        assert "✓" in html  # Léa is singing it

    async def nothing(*_args: object) -> list[SearchResult]:
        return []

    setattr(svc.searcher, "_search", nothing)  # noqa: B010
    html = client.get(f"/r/{code}/search", params={"q": "x"}).text
    assert "Les plus chantées" not in html  # a search replaces them
