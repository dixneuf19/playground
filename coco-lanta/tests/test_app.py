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


@pytest.mark.parametrize("path", ["/", "/cam", "/tv", "/regie", "/react", "/healthz"])
def test_pages(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_unknown_page(client: TestClient) -> None:
    assert client.get("/admin").status_code == 404


def test_qr_points_at_the_public_url(client: TestClient) -> None:
    response = client.get("/qr/cam.svg")
    assert response.headers["content-type"] == "image/svg+xml"
    assert client.get("/qr/tv.svg").status_code == 404


def test_camera_token_may_only_publish(client: TestClient) -> None:
    body = client.post("/api/cam", json={"name": " Denis ", "team": "faucille"}).json()
    claims = jwt.decode(body["token"], CFG.livekit_api_secret, algorithms=["HS256"])
    assert claims["sub"] == body["id"]
    assert claims["iss"] == "key"
    assert claims["name"] == "Denis"
    assert claims["video"]["canPublish"] is True
    assert claims["video"]["canSubscribe"] is False
    assert body["url"] == "wss://live.example"


def test_camera_needs_a_name_and_a_team(client: TestClient) -> None:
    assert (
        client.post("/api/cam", json={"name": " ", "team": "faucille"}).status_code
        == 422
    )
    assert (
        client.post("/api/cam", json={"name": "A", "team": "bleu"}).status_code == 422
    )


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
        with client.websocket_connect("/ws?role=cam&id=cam-1&name=Denis&team=marteau"):
            show = state(regie)
            assert show["cameras"] == [
                {"id": "cam-1", "name": "Denis", "team": "marteau"}
            ]
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
        phone.send_json({"type": "comment", "name": "", "text": "x" * 200})
        msg = receive(tv, "comment")
        assert (msg["name"], len(msg["text"])) == ("Anonyme", 80)


def test_kicked_camera_is_closed(client: TestClient) -> None:
    with (
        client.websocket_connect(REGIE) as regie,
        client.websocket_connect(
            "/ws?role=cam&id=cam-1&name=Denis&team=marteau"
        ) as cam,
    ):
        state(cam)
        regie.send_json({"type": "cmd", "cmd": "kick", "id": "cam-1"})
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                cam.receive_json()
        assert closed.value.code == 4403
