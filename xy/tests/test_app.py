import uuid

import pytest
from fastapi.testclient import TestClient

from xy import rounds
from xy.config import ROUNDS_DIR


def put(client: TestClient, positions: dict, player: uuid.UUID | None = None):
    return client.put(
        f"/api/rounds/fromages/answers/{player or uuid.uuid4()}",
        json={"positions": positions},
    )


def test_shipped_rounds_are_valid() -> None:
    assert {"centrale-supelec", "devops-platform"} <= set(rounds.load(ROUNDS_DIR))


def test_index_and_static(client: TestClient) -> None:
    assert "Deux axes" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_future_rounds_are_hidden(client: TestClient) -> None:
    assert [r["id"] for r in client.get("/api/rounds").json()] == ["fromages"]
    assert client.get("/api/rounds/plus-tard").status_code == 404
    assert client.get("/api/rounds/fromages").json()["axes"]["x"]["neg"] == "Doux"


def test_medians_and_counts(client: TestClient) -> None:
    for x in (-0.5, 0.1, 0.9):
        assert put(client, {"comte": {"x": x, "y": x}}).status_code == 204
    assert put(client, {"brie": {"x": -1, "y": 1}}).status_code == 204

    res = client.get("/api/rounds/fromages/results").json()
    assert res["players"] == 4
    by_id = {c["candidate"]: c for c in res["candidates"]}
    assert by_id["comte"]["count"] == 3
    assert by_id["comte"]["median_x"] == pytest.approx(0.1)
    assert len(by_id["comte"]["points"]) == 3
    assert "munster" not in by_id


def test_answering_again_replaces(client: TestClient) -> None:
    player = uuid.uuid4()
    put(client, {"comte": {"x": 1, "y": 1}, "brie": {"x": 0, "y": 0}}, player)
    put(client, {"comte": {"x": -1, "y": -1}}, player)

    res = client.get("/api/rounds/fromages/results").json()
    assert res["players"] == 1
    assert [(c["candidate"], c["median_x"]) for c in res["candidates"]] == [
        ("comte", -1)
    ]


@pytest.mark.parametrize(
    "positions",
    [
        {"comte": {"x": 1.5, "y": 0}},
        {"inconnu": {"x": 0, "y": 0}},
        {},
    ],
)
def test_bad_answers_are_refused(client: TestClient, positions: dict) -> None:
    assert put(client, positions).status_code == 422


def test_unknown_round_and_bad_player(client: TestClient) -> None:
    assert (
        client.put(
            f"/api/rounds/plus-tard/answers/{uuid.uuid4()}",
            json={"positions": {"a": {"x": 0, "y": 0}}},
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/rounds/fromages/answers/pas-un-uuid",
            json={"positions": {"comte": {"x": 0, "y": 0}}},
        ).status_code
        == 422
    )
