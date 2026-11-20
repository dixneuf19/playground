import secrets

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coco_lanta.app import create_app
from coco_lanta.config import Settings

# Random per run: nothing here is a real credential.
REGIE_KEY = secrets.token_urlsafe(8)
REGIE = f"/ws?role=regie&key={REGIE_KEY}"

CFG = Settings(
    public_url="https://coco.example",
    livekit_url="wss://live.example",
    livekit_api_key="key",
    livekit_api_secret=secrets.token_urlsafe(32),
    regie_password=REGIE_KEY,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(CFG))


def receive(ws, kind: str) -> dict:
    """The next message of that kind."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


def state(ws) -> dict:
    return receive(ws, "state")["show"]


@pytest.mark.parametrize("path", ["/", "/tv", "/regie", "/affiche", "/healthz"])
def test_pages(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_unknown_page(client: TestClient) -> None:
    assert client.get("/admin").status_code == 404


def test_old_qr_codes_land_on_the_guest_page(client: TestClient) -> None:
    for path in ("/cam", "/react"):
        response = client.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (307, "/")


def test_one_qr_code(client: TestClient) -> None:
    response = client.get("/qr.svg")
    assert response.headers["content-type"] == "image/svg+xml"


def test_camera_token_may_only_publish(client: TestClient) -> None:
    body = client.post("/api/cam", json={"name": " Denis "}).json()
    claims = jwt.decode(body["token"], CFG.livekit_api_secret, algorithms=["HS256"])
    assert claims["sub"] == body["id"]
    assert claims["iss"] == "key"
    assert claims["name"] == "Denis"
    assert claims["video"]["canPublish"] is True
    assert claims["video"]["canSubscribe"] is False
    assert body["url"] == "wss://live.example"


def test_camera_needs_a_name(client: TestClient) -> None:
    assert client.post("/api/cam", json={"name": " "}).status_code == 422


def test_viewer_token_may_only_watch(client: TestClient) -> None:
    body = client.post("/api/viewer").json()
    claims = jwt.decode(body["token"], CFG.livekit_api_secret, algorithms=["HS256"])
    assert claims["video"]["canPublish"] is False
    assert claims["video"]["canSubscribe"] is True


def test_regie_needs_the_password(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/ws?role=regie&key=nope") as ws,
    ):
        ws.receive_json()
    assert closed.value.code == 4401


def test_camera_joins_and_regie_runs_the_show(client: TestClient) -> None:
    with (
        client.websocket_connect(REGIE) as regie,
        client.websocket_connect("/ws?role=tv") as tv,
    ):
        assert state(regie)["cameras"] == []
        with client.websocket_connect("/ws?role=cam&id=cam-1&name=Denis"):
            show = state(regie)
            assert show["cameras"] == [{"id": "cam-1", "name": "Denis"}]
            assert show["on_air"] == ["cam-1"]

            regie.send_json(
                {"type": "cmd", "cmd": "score", "team": "marteau", "delta": 1}
            )
            assert state(regie)["scores"] == {"faucille": 0, "marteau": 1}

            regie.send_json(
                {"type": "cmd", "cmd": "banner", "text": "  Les   poteaux "}
            )
            assert state(regie)["banner"] == "Les poteaux"

            regie.send_json({"type": "cmd", "cmd": "stinger", "text": "Le conseil"})
            assert receive(tv, "stinger")["text"] == "Le conseil"


def test_only_the_regie_gives_orders(client: TestClient) -> None:
    with (
        client.websocket_connect(REGIE) as regie,
        client.websocket_connect("/ws?role=react") as phone,
    ):
        state(regie)
        phone.send_json({"type": "cmd", "cmd": "score", "team": "marteau", "delta": 1})
        regie.send_json({"type": "cmd", "cmd": "score", "team": "faucille", "delta": 1})
        assert state(regie)["scores"] == {"faucille": 1, "marteau": 0}


def test_reactions_and_comments_reach_the_tv(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws?role=tv") as tv,
        client.websocket_connect("/ws?role=react") as phone,
    ):
        state(tv)
        phone.send_json({"type": "react", "emoji": "<script>"})
        phone.send_json({"type": "react", "emoji": "🔥"})
        assert receive(tv, "react") == {"type": "react", "emoji": "🔥"}
        phone.send_json({"type": "react", "emoji": "marteau"})  # a team cheer
        assert receive(tv, "react") == {"type": "react", "emoji": "marteau"}
        phone.send_json({"type": "comment", "name": "", "text": "x" * 200})
        msg = receive(tv, "comment")
        assert (msg["name"], len(msg["text"])) == ("Anonyme", 80)


def test_kicked_camera_is_closed(client: TestClient) -> None:
    with (
        client.websocket_connect(REGIE) as regie,
        client.websocket_connect("/ws?role=cam&id=cam-1&name=Denis") as cam,
    ):
        state(cam)
        regie.send_json({"type": "cmd", "cmd": "kick", "id": "cam-1"})
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                cam.receive_json()
        assert closed.value.code == 4403


def test_wifi_password_is_shown_only_when_set(client: TestClient) -> None:
    assert "Mot de passe" not in client.get("/").text
    with_wifi = TestClient(
        create_app(Settings(wifi_name="Lagon", wifi_password="s3same"))
    )
    page = with_wifi.get("/").text
    assert "<code>Lagon</code>" in page
    assert "<code>s3same</code>" in page
