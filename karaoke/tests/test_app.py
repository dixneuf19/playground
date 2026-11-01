from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke.app import create_app
from karaoke.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = Settings(
        data_dir=tmp_path, host_password="secret", public_url="https://k.example"
    )
    return TestClient(create_app(cfg))


def test_room_creation_requires_password(client: TestClient) -> None:
    assert client.post("/api/rooms", json={"password": "nope"}).status_code == 403
    res = client.post("/api/rooms", json={"password": "secret"})
    assert res.status_code == 200
    code = res.json()["code"]

    room = client.get(f"/api/rooms/{code}").json()
    assert room["join_url"] == f"https://k.example/r/{code}"
    assert client.get(f"/r/{code}").status_code == 200
    assert client.get(f"/display/{code}").status_code == 200
    assert (
        client.get(f"/api/rooms/{code}/qr.svg").headers["content-type"]
        == "image/svg+xml"
    )


def test_unknown_room_is_404(client: TestClient) -> None:
    assert client.get("/r/ZZZZ").status_code == 404
    assert client.get("/api/rooms/ZZZZ").status_code == 404


def test_queue_over_websocket(client: TestClient) -> None:
    code = client.post("/api/rooms", json={"password": "secret"}).json()["code"]
    hub = client.app.state.hub  # type: ignore[attr-defined]
    hub.ensure_download = lambda _video_id: None  # no network in unit tests

    with client.websocket_connect(f"/ws/{code}") as guest:
        assert guest.receive_json()["room"]["queue"] == []
        guest.send_json(
            {"type": "add", "video_id": "abc", "title": "Song", "singer": "Léa"}
        )
        state = guest.receive_json()["room"]
        assert [(i["title"], i["singer"]) for i in state["queue"]] == [("Song", "Léa")]

        guest.send_json({"type": "remove", "item_id": state["queue"][0]["id"]})
        assert guest.receive_json()["room"]["queue"] == []


def test_media_404_when_not_cached(client: TestClient) -> None:
    assert client.get("/media/nothere.mp4").status_code == 404


def test_qr_is_a_standalone_svg(client: TestClient) -> None:
    code = client.post("/api/rooms", json={"password": "secret"}).json()["code"]
    svg = client.get(f"/api/rooms/{code}/qr.svg").text
    # Without the namespace, browsers render an <img src=...svg> blank.
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg


def test_join_url_replaces_localhost_with_lan_ip(tmp_path: Path) -> None:
    client = TestClient(
        create_app(Settings(data_dir=tmp_path)), base_url="http://localhost:8000"
    )
    code = client.post("/api/rooms", json={}).json()["code"]
    url = client.get(f"/api/rooms/{code}").json()["join_url"]
    assert "localhost" not in url
    assert url.endswith(f":8000/r/{code}")


def test_cancelled_flag_is_set_by_server(client: TestClient) -> None:
    code = client.post("/api/rooms", json={"password": "secret"}).json()["code"]
    hub = client.app.state.hub  # type: ignore[attr-defined]
    hub.ensure_download = lambda _video_id: None

    with client.websocket_connect(f"/ws/{code}") as guest:
        guest.receive_json()
        # The phone does not (and cannot) say the artist is cancelled.
        guest.send_json(
            {
                "type": "add",
                "video_id": "x",
                "title": "Les lacs du Connemara",
                "artist": "Michel Sardou",
                "singer": "Tonton",
                "cancelled": None,
            }
        )
        item = guest.receive_json()["room"]["queue"][0]
        assert item["cancelled"].startswith("Michel Sardou")
