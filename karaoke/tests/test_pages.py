"""The htmx phone page: server-rendered HTML, actions over HTTP, fragments
pushed over the WebSocket."""

import json
import re
from collections.abc import Callable
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from karaoke import db
from karaoke.app import create_app
from karaoke.config import Settings
from karaoke.reactions import BURST, ROOM_EMOJI_BURST
from karaoke.search import SearchResult
from tests.conftest import claim, freeze_reactions, phone, services

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
VID = "abcdefghijk"


def _room(client: TestClient) -> str:
    return client.post("/api/rooms", json={"password": "secret"}).json()["code"]


def test_page_asks_for_a_name_until_it_has_one(client: TestClient) -> None:
    code = _room(client)
    assert 'class="sheet" id="name-sheet"' in client.get(f"/r/{code}").text
    page = claim(client, code, "Léa").get(f"/r/{code}").text
    assert 'class="sheet hidden" id="name-sheet"' in page
    assert "🎤 <span>Léa</span>" in page
    assert f'hx-ws:connect="/ws/{code}?role=guest&view=html"' in page
    assert page.count('id="everywhere"') == 1
    # The search bar takes links ("Titre, artiste ou lien YouTube…"): no
    # separate paste button in the intro
    assert "lien YouTube" in page and "data-paste" not in page


def test_the_name_is_per_room_and_the_last_one_only_suggested(
    client: TestClient,
) -> None:
    first, second = _room(client), _room(client)
    claim(client, first, "Léa")
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
    claim(client, first, "Léa")
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
    claim(client, second, "Anonyme")
    assert f'id="ph-{theirs}"' in client.get(f"/r/{second}/state").text


def test_add_takes_the_singer_from_the_cookie(client: TestClient) -> None:
    code = _room(client)
    res = claim(client, code, "Léa").post(
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
    page = claim(client, code, "Léa").get(f"/r/{code}").text
    assert 'data-react="fire"' in page and 'data-react="confetti"' in page
    # effects are emoji-only buttons too, named for screen readers
    assert 'aria-label="Confettis">🎉</button>' in page
    assert 'aria-label="Pluie de cœurs">💖</button>' in page
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


def _fire_on_screen(client: TestClient, code: str, phones: list[TestClient]) -> int:
    """Each phone claims a name and sends 10 🔥; how many reach the screen."""
    do = f"/r/{code}/do"
    with client.websocket_connect(f"/ws/{code}?role=display") as screen:
        screen.receive_json()
        for n, p in enumerate(phones):
            claim(p, code, f"bot-{n}").post(
                do, data={"type": "react", "kind": "fire", "count": 10}
            )
        client.post(do, data={"type": "volume", "value": 0.5})  # NOTIFYs keep order
        fire = 0
        while True:
            msg = screen.receive_json()
            if msg["type"] != "command":
                continue  # claiming a name pushes the state
            if msg["action"] != "react":
                break
            fire += msg["count"]
        assert msg["action"] == "volume"
    return fire


def test_renaming_gives_no_fresh_reaction_bucket(client: TestClient) -> None:
    freeze_reactions(client)
    code = _room(client)
    assert _fire_on_screen(client, code, [client] * 20) == BURST  # one phone


def test_rotating_phones_cannot_flood_the_room(client: TestClient) -> None:
    freeze_reactions(client)
    code = _room(client)
    phones = [phone(client) for _ in range(20)]  # a fresh token each time
    assert _fire_on_screen(client, code, phones) == ROOM_EMOJI_BURST


def test_pushed_fragments_are_personal_and_escaped(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
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
    tom = claim(phone(client), code, "Tom")
    assert "tag-me" not in tom.get(f"/r/{code}/state").text


def _youtube(client: TestClient) -> None:
    """Fake searches: a link lookup knows every id but "zzzzzzzzzzz"."""

    async def fake(_q: str, _everywhere: bool, link: str | None) -> list:
        if link == "zzzzzzzzzzz":
            return []
        return [
            SearchResult(
                video_id=link or VID,
                title="Song",
                artist="Singer",
                raw_title="Song - Singer",
                channel="KaraFun",
                duration=None,
                thumbnail="t",
                curated=not link,
            )
        ]

    setattr(services(client).searcher, "_search", fake)  # noqa: B010


def _check_titles(client: TestClient) -> None:
    """One pass of the leader's check of new favourites' titles."""
    svc = services(client)
    svc.worker.check_titles = True
    portal = client.portal
    assert portal is not None
    portal.call(svc.worker.check_favourites)


def test_favourites_are_shared_by_every_room(client: TestClient) -> None:
    _youtube(client)
    first, second = _room(client), _room(client)
    claim(client, second, "Tom")
    lea = claim(phone(client), first, "Léa")
    page = client.get(f"/r/{second}").text
    assert "Aucun favori" in page  # nothing hearted yet
    # Their own tab, out of the search one
    assert 'data-view="favourites"' in page
    search = page[page.index('id="view-search"') : page.index('id="view-queue"')]
    tab = page[page.index('id="view-favourites"') :]
    assert 'id="favourites"' not in search and 'id="favourites"' in tab
    with client.websocket_connect(f"/ws/{second}?view=html") as tom:
        tom.receive_text()
        # Léa hearts a song in the first room: saved at once, phone's title
        res = lea.post(
            f"/r/{first}/heart",
            data={"type": "heart", "video_id": VID, "title": "Phone", "on": "true"},
        )
        assert res.status_code == 200
        assert 'aria-pressed="true"' in res.text  # the button, now on
        assert "Retirer des favoris ?" in res.text  # removing asks first
        # Tom's phone, in the other room, gets the list by push
        pushed = tom.receive_text()
        assert f'id="fav-{VID}"' in pushed and "Phone" in pushed
        # The leader checks it with YouTube: its title replaces the phone's
        _check_titles(client)
        pushed = tom.receive_text()
        assert "Singer" in pushed and "Phone" not in pushed
        # A plain room push leaves the (heavy) list out
        client.post(f"/r/{second}/do", data={"type": "add", "video_id": VID})
        pushed = tom.receive_text()
    assert 'id="queue-panel"' in pushed and 'id="favourites"' not in pushed
    # Anyone may take it out
    res = client.post(
        f"/r/{second}/heart", data={"type": "heart", "video_id": VID, "on": "false"}
    )
    assert 'aria-pressed="false"' in res.text
    assert f'id="fav-{VID}"' not in client.get(f"/r/{first}").text
    bad = {"type": "heart", "video_id": "../x", "on": "true"}
    assert client.post(f"/r/{first}/heart", data=bad).status_code == 422
    skip = {"type": "skip"}
    assert client.post(f"/r/{first}/heart", data=skip).status_code == 422
    assert client.post("/r/ZZZZ/heart", data=bad).status_code == 404


def test_a_favourite_youtube_does_not_know_is_dropped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _youtube(client)
    monkeypatch.setattr(db, "RECHECK_SECONDS", 0)  # retry at once
    code = _room(client)
    fake = {"type": "heart", "video_id": "zzzzzzzzzzz", "title": "Fake", "on": 1}
    assert client.post(f"/r/{code}/heart", data=fake).status_code == 200
    for _ in range(db.MAX_FAILED_CHECKS - 1):
        _check_titles(client)
        assert 'id="fav-zzzzzzzzzzz"' in client.get(f"/r/{code}").text
    _check_titles(client)
    assert 'id="fav-zzzzzzzzzzz"' not in client.get(f"/r/{code}").text


def test_queued_songs_and_results_show_their_heart(client: TestClient) -> None:
    _youtube(client)
    code = _room(client)
    claim(client, code, "Léa")
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": VID})
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": "kkkkkkkkkkk"})
    client.post(f"/r/{code}/do", data={"type": "heart", "video_id": VID, "on": 1})
    state = client.get(f"/r/{code}/state").text
    assert state.count('aria-pressed="true"') == 1  # the hearted queued song
    assert state.count('aria-pressed="false"') == 1  # the other one
    assert f'id="fav-{VID}"' in client.get(f"/r/{code}").text

    html = client.get(f"/r/{code}/search", params={"q": "song"}).text
    assert 'aria-pressed="true"' in html


def test_only_the_uploader_sees_their_waiting_photos(client: TestClient) -> None:
    code = _room(client)
    url = f"/api/rooms/{code}/photos"
    claim(client, code, "Léa")
    client.post(url, content=JPEG)  # goes on screen
    waiting = client.post(url, content=JPEG).json()["id"]
    assert f'id="ph-{waiting}"' in client.get(f"/r/{code}/state").text
    tom = claim(phone(client), code, "Tom")
    assert waiting not in tom.get(f"/r/{code}/state").text
    # Tom can't cancel it: the uploader is his phone's name, not the form
    tom.post(
        f"/r/{code}/do",
        data={"type": "photo_remove", "photo_id": waiting, "uploader": "Léa"},
    )
    assert f'id="ph-{waiting}"' in client.get(f"/r/{code}/state").text
    client.post(f"/r/{code}/do", data={"type": "photo_remove", "photo_id": waiting})
    assert waiting not in client.get(f"/r/{code}/state").text


def test_anyone_cancels_and_uncancels_an_artist(client: TestClient) -> None:
    async def fake(_q: str, _everywhere: bool, _link: str | None) -> list[SearchResult]:
        return [
            SearchResult(
                video_id=VID,
                title="L'aventurier",
                artist="Indochine",
                raw_title="",
                channel="KaraFun",
                duration=200,
                thumbnail="t",
                curated=True,
            )
        ]

    setattr(services(client).searcher, "_search", fake)  # noqa: B010
    code = _room(client)
    claim(client, code, "Léa")

    def search() -> str:
        # Cached by the searcher, so only the flags change between calls
        return client.get(f"/r/{code}/search", params={"q": "aventurier"}).text

    html = search()
    assert "J'assume" not in html
    assert '"cancel": null' in html

    refused = client.post(
        f"/r/{code}/do", data={"type": "cancel_artist", "artist": "Indochine"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Donne une raison"

    done = client.post(
        f"/r/{code}/do",
        # `by` is the cookie's name, not what the form says
        data={
            "type": "cancel_artist",
            "artist": "Indochine",
            "reason": "Trop triste",
            "by": "Tom",
        },
    )
    assert done.status_code == 204
    assert json.loads(done.headers["HX-Trigger"]) == {
        "toast": "🚫 Indochine est cancel",
        "cancelled": True,
    }
    html = search()
    assert 'data-confirm-yes="J\'assume"' in html
    assert "Indochine : Trop triste (cancel par Léa)." in html
    assert '"by": "L\\u00e9a"' in html  # for the artist sheet

    done = client.post(
        f"/r/{code}/do", data={"type": "uncancel_artist", "artist": "INDOCHINE"}
    )
    assert json.loads(done.headers["HX-Trigger"])["toast"] == (
        "INDOCHINE n'est plus cancel"
    )
    assert "J'assume" not in search()
    again = client.post(
        f"/r/{code}/do", data={"type": "uncancel_artist", "artist": "Indochine"}
    )
    assert json.loads(again.headers["HX-Trigger"])["toast"] == (
        "Indochine n'était pas cancel"
    )


def test_popular_songs_with_a_mis_parsed_title_are_flagged(
    client: TestClient,
) -> None:
    svc = services(client)
    path = svc.library.path_for(VID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")
    portal = client.portal
    assert portal is not None
    portal.call(svc.store.video_status, VID)
    # split_title() reads this as song "Michel Sardou", artist "La maladie…"
    title = "Michel Sardou - La maladie d'amour | Paroles"
    portal.call(svc.store.finish_download, VID, True, title, 1)
    code = _room(client)
    claim(client, code, "Léa")
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": VID})
    html = client.get(f"/r/{code}/search").text
    assert "Les plus chantées" in html
    assert 'data-confirm-yes="J\'assume"' in html


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
        claim(client, code, "Léa")
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
    page = claim(client, code, "Léa").get(f"/r/{code}").text
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
    assert 'class="heart"' in player  # the playing song can be hearted
    _youtube(client)
    client.post(f"/r/{code}/heart", data={"type": "heart", "video_id": VID, "on": 1})
    state = client.get(f"/r/{code}/state").text
    player = state[state.index('id="player"') : state.index('id="queue-panel"')]
    assert 'aria-pressed="true"' in player
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
    claim(client, code, "Léa")
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
        assert 'aria-label="Ajouter aux favoris"' in html  # the shared card
        assert 'data-confirm-yes="J\'assume"' in html  # cancelled: asks first
        assert "✓" in html  # Léa is singing it

    async def nothing(*_args: object) -> list[SearchResult]:
        return []

    setattr(svc.searcher, "_search", nothing)  # noqa: B010
    html = client.get(f"/r/{code}/search", params={"q": "x"}).text
    assert "Les plus chantées" not in html  # a search replaces them


def test_a_name_belongs_to_one_phone(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    client.post(f"/api/rooms/{code}/photos", content=JPEG)  # goes on screen
    waiting = client.post(f"/api/rooms/{code}/photos", content=JPEG).json()["id"]
    other = phone(client)
    res = other.post(f"/r/{code}/name", data={"name": "Léa"})
    assert res.status_code == 409
    assert res.json() == {
        "detail": "«\u00a0Léa\u00a0» est déjà pris dans cette salle",
        "suggestion": "Léa 2",
    }
    # Not normalized: another spelling is another name
    claim(other, code, "léa")
    assert waiting not in other.get(f"/r/{code}/state").text
    claim(other, code, "Léa 2")
    res = other.post(f"/r/{code}/name", data={"name": "Léa"})
    assert res.json()["suggestion"] == "Léa 3"
    assert other.post(f"/r/{code}/name", data={"name": " "}).status_code == 422
    assert other.post("/r/ZZZZ/name", data={"name": "x"}).status_code == 404


def test_renaming_takes_songs_and_photos_along(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    client.post(f"/api/rooms/{code}/photos", content=JPEG)  # goes on screen
    waiting = client.post(f"/api/rooms/{code}/photos", content=JPEG).json()["id"]
    client.post(f"/r/{code}/do", data={"type": "add", "video_id": VID})
    claim(client, code, "Lou")
    page = client.get(f"/r/{code}").text
    assert "🎤 <span>Lou</span>" in page
    assert f'id="ph-{waiting}"' in page and "tag-me" in page
    room = client.get(f"/api/rooms/{code}").json()
    assert [i["singer"] for i in room["queue"]] == ["Lou"]
    # The old name is free at once, and comes with nothing
    other = claim(phone(client), code, "Léa")
    state = other.get(f"/r/{code}/state").text
    assert waiting not in state and "tag-me" not in state


def test_a_gone_phone_releases_its_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    client.post(f"/api/rooms/{code}/photos", content=JPEG)  # goes on screen
    waiting = client.post(f"/api/rooms/{code}/photos", content=JPEG).json()["id"]
    monkeypatch.setattr(db, "NAME_RELEASE_SECONDS", -1)  # gone long enough
    other = phone(client)
    with client.websocket_connect(f"/ws/{code}?view=html") as lea:
        assert 'data-me="Léa"' in lea.receive_text()
        claim(other, code, "Léa")  # a new phone simply takes it
        pushed = lea.receive_text()
    # The old phone lost the name on the next push; the new one has its photos
    assert 'data-me=""' in pushed and "🎤 <span>?</span>" in pushed
    assert f'id="ph-{waiting}"' in other.get(f"/r/{code}/state").text
    assert waiting not in client.get(f"/r/{code}/state").text


def test_an_open_socket_keeps_the_name(client: TestClient) -> None:
    code = _room(client)
    svc, portal = services(client), client.portal
    assert portal is not None

    def gone() -> None:
        portal.call(
            svc.store.pool.execute,
            "UPDATE guests SET last_seen = now() - interval '1 hour'",
        )

    def claimable() -> bool:
        res = phone(client).post(f"/r/{code}/name", data={"name": "Léa"})
        return res.status_code == 204

    claim(client, code, "Léa")
    assert not claimable()  # just claimed: still here
    gone()
    with client.websocket_connect(f"/ws/{code}?view=html") as lea:
        lea.receive_text()  # sent once the socket counted as present
        assert not claimable()  # connecting counts as being here
        gone()
        portal.call(svc.hub.heartbeat)  # every 30 s
        assert not claimable()
    gone()
    assert claimable()


def test_a_name_from_before_claims_is_kept_once(client: TestClient) -> None:
    code = _room(client)
    old = f"karaoke_name_{code}"
    client.cookies.set(old, quote("Léa"))  # set by guest.js before
    res = client.get(f"/r/{code}")
    assert "🎤 <span>Léa</span>" in res.text
    # Read once, then deleted
    assert f'{old}=""; expires' in res.headers["set-cookie"]
    # Someone else already holds it: the sheet opens, prefilled
    other = phone(client)
    other.cookies.set(old, quote("Léa"))
    page = other.get(f"/r/{code}").text
    assert 'class="sheet" id="name-sheet"' in page
    assert 'value="Léa"' in page


def _token_cookie(client: TestClient) -> str:
    code = _room(client)
    res = client.post(f"/r/{code}/name", data={"name": "Léa"})
    return next(
        h for h in res.headers.get_list("set-cookie") if h.startswith("karaoke_guest=")
    )


def test_the_token_cookie_is_secure_behind_https(
    client: TestClient, make_settings: Callable[..., Settings]
) -> None:
    assert "Secure" not in _token_cookie(client)  # public URL in http
    cfg = make_settings(host_password="secret", public_url="https://k.example")
    with TestClient(create_app(cfg), base_url="https://testserver") as https:
        token = _token_cookie(https)
    assert "Secure" in token and "HttpOnly" in token
