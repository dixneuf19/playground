from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from karaoke import db
from karaoke.app import create_app
from karaoke.config import Settings
from karaoke.reactions import BURST
from tests.conftest import claim, freeze_reactions, phone, services

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def _room(client: TestClient) -> str:
    return client.post("/api/rooms", json={"password": "secret"}).json()["code"]


def _until(ws: WebSocketTestSession, check: Callable[[dict[str, Any]], bool]) -> dict:
    """Read messages until one matches (state pushes can arrive in bursts)."""
    for _ in range(50):
        msg = ws.receive_json()
        if check(msg):
            return msg
    raise AssertionError("expected message never came")


def _state(ws: WebSocketTestSession, check: Callable[[dict[str, Any]], bool]) -> dict:
    return _until(ws, lambda m: m["type"] == "state" and check(m["room"]))["room"]


def _no_downloads(client: TestClient) -> None:
    services(client).worker.download_videos = False


def test_room_creation_requires_password(client: TestClient) -> None:
    assert client.post("/api/rooms", json={"password": "nope"}).status_code == 403
    code = _room(client)

    room = client.get(f"/api/rooms/{code}").json()
    assert room["join_url"] == f"http://k.example/r/{code}"
    assert client.get(f"/r/{code}").status_code == 200
    assert client.get(f"/r/{code.lower()}").status_code == 200
    assert client.get(f"/display/{code}").status_code == 200


def test_unknown_room_is_404(client: TestClient) -> None:
    assert client.get("/r/ZZZZ").status_code == 404
    assert client.get("/api/rooms/ZZZZ").status_code == 404


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_qr_is_a_standalone_svg(client: TestClient) -> None:
    svg = client.get(f"/api/rooms/{_room(client)}/qr.svg")
    assert svg.headers["content-type"] == "image/svg+xml"
    # Without the namespace, browsers render an <img src=...svg> blank.
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg.text


def test_join_url_replaces_localhost_with_lan_ip(
    make_settings: Callable[..., Settings],
) -> None:
    app = create_app(make_settings())
    with TestClient(app, base_url="http://localhost:8000") as c:
        code = c.post("/api/rooms", json={}).json()["code"]
        url = c.get(f"/api/rooms/{code}").json()["join_url"]
    assert "localhost" not in url
    assert url.endswith(f":8000/r/{code}")


def test_queue_over_websocket(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        guest.send_json({"type": "add", "video_id": "abcdefghijk", "title": "Song"})
        state = _state(guest, lambda r: r["queue"])
        assert [(i["title"], i["singer"]) for i in state["queue"]] == [("Song", "Léa")]

        guest.send_json({"type": "remove", "item_id": state["queue"][0]["id"]})
        _state(guest, lambda r: r["queue"] == [])


def test_cancelled_flag_is_set_by_server(client: TestClient) -> None:
    code = _room(client)
    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        # The phone does not (and cannot) say the artist is cancelled.
        guest.send_json(
            {
                "type": "add",
                "video_id": "xxxxxxxxxxx",
                "title": "Les lacs du Connemara",
                "artist": "Michel Sardou",
                "singer": "Tonton",
                "cancelled": None,
            }
        )
        item = _state(guest, lambda r: r["queue"])["queue"][0]
        assert item["cancelled"].startswith("Michel Sardou")


def test_queue_survives_a_restart(make_settings: Callable[..., Settings]) -> None:
    cfg = make_settings()
    with TestClient(create_app(cfg)) as first:
        _no_downloads(first)
        code = first.post("/api/rooms", json={}).json()["code"]
        with first.websocket_connect(f"/ws/{code}") as guest:
            guest.receive_json()
            guest.send_json({"type": "add", "video_id": "abcdefghijk", "title": "Song"})
            _state(guest, lambda r: r["queue"])
    with TestClient(create_app(cfg)) as second:
        room = second.get(f"/api/rooms/{code}").json()
        assert [i["title"] for i in room["queue"]] == ["Song"]


def test_two_replicas_share_rooms(make_settings: Callable[..., Settings]) -> None:
    cfg = make_settings()
    with TestClient(create_app(cfg)) as a, TestClient(create_app(cfg)) as b:
        _no_downloads(a)
        _no_downloads(b)
        code = a.post("/api/rooms", json={}).json()["code"]
        with (
            b.websocket_connect(f"/ws/{code}?role=display") as screen_on_b,
            a.websocket_connect(f"/ws/{code}") as guest_on_a,
        ):
            screen_on_b.receive_json()
            guest_on_a.receive_json()
            guest_on_a.send_json(
                {"type": "add", "video_id": "abcdefghijk", "title": "Song"}
            )
            # Pushed to the screen through NOTIFY, from the other replica
            state = _state(screen_on_b, lambda r: r["queue"])
            assert state["queue"][0]["title"] == "Song"

            guest_on_a.send_json({"type": "volume", "value": 0.4})
            cmd = _until(screen_on_b, lambda m: m["type"] == "command")
            assert cmd["action"] == "volume"
            assert abs(cmd["value"] - 0.4) < 1e-6

            guest_on_a.send_json({"type": "react", "kind": "clap"})
            cmd = _until(screen_on_b, lambda m: m["type"] == "command")
            assert cmd == {
                "type": "command",
                "action": "react",
                "emoji": "👏",
                "count": 1,
            }


def test_reactions_reach_screens_only_and_are_throttled_per_socket(
    client: TestClient,
) -> None:
    freeze_reactions(client)
    code = _room(client)
    with (
        client.websocket_connect(f"/ws/{code}?role=display") as screen,
        client.websocket_connect(f"/ws/{code}") as guest,
    ):
        screen.receive_json()
        guest.receive_json()
        for _ in range(5):  # a forged sender changes nothing: the socket is the key
            guest.send_json(
                {"type": "react", "kind": "fire", "count": 10, "sender": "x"}
            )
        guest.send_json({"type": "volume", "value": 0.5})  # NOTIFYs keep their order
        fire = 0
        while (cmd := _until(screen, lambda m: m["type"] == "command"))["action"] == (
            "react"
        ):
            assert cmd["emoji"] == "🔥"
            fire += cmd["count"]
        assert cmd["action"] == "volume"
        assert fire == BURST
        # the guest socket gets the volume's state push, never the commands
        assert guest.receive_json()["type"] == "state"


def test_a_phone_s_sockets_share_one_reaction_bucket(client: TestClient) -> None:
    freeze_reactions(client)
    code = _room(client)
    claim(client, code, "Léa")  # the phone now has a token
    with (
        client.websocket_connect(f"/ws/{code}?role=display") as screen,
        client.websocket_connect(f"/ws/{code}") as tab_1,
        client.websocket_connect(f"/ws/{code}") as tab_2,
    ):
        for ws in (screen, tab_1, tab_2):
            ws.receive_json()
        for ws in (tab_1, tab_2, tab_1, tab_2, tab_1, tab_2):
            ws.send_json({"type": "react", "kind": "fire", "count": 10})
        fire = 0
        while fire < BURST:  # each tab alone would get BURST
            fire += _until(screen, lambda m: m["type"] == "command")["count"]
        tab_2.send_json({"type": "volume", "value": 0.5})
        assert _until(screen, lambda m: m["type"] == "command")["action"] == "volume"
    assert fire == BURST


def test_names_are_shared_by_the_replicas(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings()
    with TestClient(create_app(cfg)) as a, TestClient(create_app(cfg)) as b:
        _no_downloads(a)
        _no_downloads(b)
        code = a.post("/api/rooms", json={}).json()["code"]
        assert a.post(f"/r/{code}/name", data={"name": "Léa"}).status_code == 204
        with a.websocket_connect(f"/ws/{code}?view=html") as lea_on_a:
            assert 'data-me="Léa"' in lea_on_a.receive_text()
            # Another phone, served by the other replica
            assert b.post(f"/r/{code}/name", data={"name": "Léa"}).status_code == 409
            monkeypatch.setattr(db, "NAME_RELEASE_SECONDS", -1)  # gone long enough
            assert b.post(f"/r/{code}/name", data={"name": "Léa"}).status_code == 204
            # Replica a pushes the change to the phone that lost the name
            assert 'data-me=""' in lea_on_a.receive_text()


def test_media_404_when_not_cached(client: TestClient) -> None:
    assert client.get("/media/nothere.mp4").status_code == 404


def test_photo_upload_is_validated_and_anonymous(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    url = f"/api/rooms/{code}/photos"
    assert client.post(url, content=b"not an image").status_code == 415

    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        res = client.post(url, content=JPEG, headers={"Content-Type": "image/jpeg"})
        assert res.status_code == 200
        photo_id = res.json()["id"]

        # The rotation loop puts it on screen
        state = _state(guest, lambda r: r["photo"] is not None)
        assert state["photo"] == {"id": photo_id}
        assert "Léa" not in str(state)

        served = client.get(f"/photos/{code}/{photo_id}")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"


def test_photos_are_mixed_fairly(client: TestClient) -> None:
    code = _room(client)
    phones = {
        "lea": claim(client, code, "lea"),
        "tom": claim(phone(client), code, "tom"),
    }
    ids = {}
    for who in ("lea", "lea", "lea", "tom"):
        res = phones[who].post(f"/api/rooms/{code}/photos", content=JPEG)
        ids[res.json()["id"]] = who
    room = client.get(f"/api/rooms/{code}").json()
    order = ([room["photo"]["id"]] if room["photo"] else []) + [
        p["id"] for p in room["photos"]
    ]
    assert [ids[i] for i in order] == ["lea", "tom", "lea", "lea"]


def test_photo_cancel_needs_the_uploader(client: TestClient) -> None:
    code = _room(client)
    lea = claim(client, code, "Léa")
    url = f"/api/rooms/{code}/photos"
    lea.post(url, content=JPEG)  # goes on screen
    waiting = lea.post(url, content=JPEG).json()["id"]

    tom = claim(phone(client), code, "Tom")
    with tom.websocket_connect(f"/ws/{code}") as on_tom:
        on_tom.receive_json()
        # Saying "Léa" doesn't make Tom her: the name comes from his phone
        on_tom.send_json(
            {"type": "photo_remove", "photo_id": waiting, "uploader": "Léa"}
        )
        on_tom.send_json({"type": "volume", "value": 0.5})  # handled after it
        state = _state(on_tom, lambda r: r["volume"] == 0.5)
        assert waiting in [p["id"] for p in state["photos"]]
    with lea.websocket_connect(f"/ws/{code}") as on_lea:
        on_lea.receive_json()
        on_lea.send_json({"type": "photo_remove", "photo_id": waiting})
        _state(on_lea, lambda r: all(p["id"] != waiting for p in r["photos"]))


def test_names_come_from_the_phone_never_the_request(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "Léa")
    nameless = phone(client)
    forged = [
        nameless.post(f"/api/rooms/{code}/photos?uploader=Léa", content=JPEG)
        for _ in range(2)
    ]
    assert all(r.status_code == 200 for r in forged)
    leas_page = client.get(f"/r/{code}/state").text
    assert all(r.json()["id"] not in leas_page for r in forged)
    for n, (who, expected) in enumerate(((nameless, "Anonyme"), (client, "Léa")), 1):
        with who.websocket_connect(f"/ws/{code}") as ws:
            ws.receive_json()
            ws.send_json({"type": "add", "video_id": "abcdefghijk", "singer": "Liar"})
            state = _state(ws, lambda r, n=n: len(r["queue"]) == n)
            assert state["queue"][-1]["singer"] == expected


def test_photo_limit_per_uploader(client: TestClient) -> None:
    code = _room(client)
    claim(client, code, "spam")
    url = f"/api/rooms/{code}/photos"
    statuses = [client.post(url, content=JPEG).status_code for _ in range(32)]
    assert 429 in statuses


def test_reloaded_screen_gets_the_playback_position(client: TestClient) -> None:
    svc = services(client)
    store = svc.store
    path = svc.library.path_for("abcdefghijk")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake mp4")
    portal = client.portal
    assert portal is not None
    portal.call(store.video_status, "abcdefghijk")  # registers the video
    portal.call(store.finish_download, "abcdefghijk", True, "Song", 1)
    code = _room(client)
    with client.websocket_connect(f"/ws/{code}?role=display") as screen:
        screen.receive_json()
        screen.send_json({"type": "add", "video_id": "abcdefghijk", "title": "Song"})
        item_id = _state(screen, lambda r: r["current"])["current"]["id"]
        screen.send_json({"type": "player", "state": "playing", "t": 0})
        screen.send_json({"type": "position", "item_id": item_id, "t": 42.0})
        screen.send_json({"type": "position", "item_id": "stale", "t": 99.0})
        screen.send_json({"type": "pause"})
        state = _state(screen, lambda r: r["state"] == "paused")
    assert 42 <= state["position"] < 45
    # A fresh screen (the reload) is told where to resume
    with client.websocket_connect(f"/ws/{code}?role=display") as again:
        room = _state(again, lambda _r: True)
    assert room["position"] == state["position"]


def test_add_rejects_anything_but_a_youtube_id(client: TestClient) -> None:
    code = _room(client)
    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        for bad in ("abc&x=/../../../../escaped/evil", "../../etc", 42, None):
            guest.send_json({"type": "add", "video_id": bad, "title": "Evil"})
        guest.send_json({"type": "add", "video_id": "abcdefghijk", "title": "Ok"})
        room = _state(guest, lambda r: r["queue"])
    assert [i["title"] for i in room["queue"]] == ["Ok"]
    # The thumbnail comes from the server, whatever the phone sends
    assert room["queue"][0]["thumbnail"].startswith(
        "https://i.ytimg.com/vi/abcdefghijk/"
    )


def test_search_needs_a_live_room(client: TestClient) -> None:
    assert (
        client.get("/api/search", params={"q": "x", "room": "ZZZZ"}).status_code == 404
    )
    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_media_rejects_odd_ids(client: TestClient) -> None:
    assert client.get("/media/..%2F..%2Fetc.mp4").status_code == 404


def test_one_leader_and_a_quick_takeover(
    make_settings: Callable[..., Settings],
) -> None:
    cfg = make_settings()
    first, second = TestClient(create_app(cfg)), TestClient(create_app(cfg))
    with first:
        leader = services(first).worker.leadership
        assert leader.is_leader  # the first replica up takes the lock at once
        second.__enter__()
        try:
            follower = services(second).worker.leadership
            portal = second.portal
            assert portal is not None
            portal.call(follower.step)  # a check, as the poll loop would do
            assert not follower.is_leader
            first.__exit__(None, None, None)  # the leader stops: lock released
            portal.call(follower.step)
            assert follower.is_leader
        finally:
            second.__exit__(None, None, None)


def test_leader_downloads_a_queued_song_and_starts_it(client: TestClient) -> None:
    svc = services(client)

    def fake_download(video_id: str, _worker: str, on_progress: Any) -> tuple[str, int]:
        on_progress(0.5)
        svc.library.path_for(video_id).write_bytes(b"fake mp4")
        return "Song", 8

    setattr(svc.library, "download", fake_download)  # noqa: B010
    svc.worker.download_videos = True
    code = _room(client)
    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        guest.send_json({"type": "add", "video_id": "abcdefghijk", "title": "Song"})
        room = _state(guest, lambda r: r["current"] is not None)
    assert room["current"]["status"] == "ready"
    assert room["state"] == "playing"
