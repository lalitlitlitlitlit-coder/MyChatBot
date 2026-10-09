import socket

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ACCESS_CODE = "correct-horse-battery"
PROFILE = {
    "display_name": "Maya",
    "age": 16,
    "playing_level": "national_junior",
    "pressure_points": "Serving when ahead in the set",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """NFR-05: any attempt to open a network connection fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("Tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        reviewer_access_code=ACCESS_CODE,
        session_secret="test-secret",
        model_provider="fake",
        model_timeout_seconds=0.2,
        database_path=tmp_path / "test.db",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def db(app):
    return app.state.db


@pytest.fixture
def new_client(app):
    """Each call returns a fresh browser (its own cookie jar)."""
    return lambda: TestClient(app)


@pytest.fixture
def anonymous(new_client):
    return new_client()


@pytest.fixture
def player(new_client):
    """A browser that already has a player cookie, but no profile."""
    client = new_client()
    client.player_id = client.post("/api/player").json()["player_id"]
    return client


@pytest.fixture
def reviewer(new_client):
    client = new_client()
    assert client.post("/api/review/unlock", json={"access_code": ACCESS_CODE}).status_code == 204
    return client


@pytest.fixture
def model(app):
    """The scripted fake model behind the app."""
    return app.state.model


@pytest.fixture
def ready(player):
    """A player who has finished onboarding."""
    assert player.post("/api/profile", json=PROFILE).status_code == 201
    return player


def start(client, task_type="post_loss"):
    response = client.post("/api/sessions", json={"task_type": task_type})
    assert response.status_code == 201, response.text
    return response.json()["session"]["id"]


def say(client, session_id, content):
    return client.post(f"/api/sessions/{session_id}/messages", json={"content": content})
