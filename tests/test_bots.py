"""Several chatbots, each a folder under bots/ (added after the spec, at the owner's request)."""
import json
import shutil
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT
from app.db import Database
from app.main import create_app
from app.model_client import COACH, SAFETY, SUMMARY
from tests.conftest import PROFILE, say, start


def start_bot(client, bot_id, task_type="chat"):
    response = client.post("/api/sessions", json={"bot_id": bot_id, "task_type": task_type})
    assert response.status_code == 201, response.text
    return response.json()


def test_bots_are_listed_with_tennis_first(player):
    bots = player.get("/api/bots").json()
    assert [bot["id"] for bot in bots][:1] == ["tennis"]
    by_id = {bot["id"]: bot for bot in bots}
    assert by_id["tennis"]["needs_profile"] is True and by_id["tennis"]["has_memory"] is True
    assert by_id["assistant"]["needs_profile"] is False and by_id["assistant"]["has_memory"] is False
    assert by_id["assistant"]["tasks"] == [
        {"id": "chat", "button": "Start a chat", "title": "Chat", "result_label": "Result"}
    ]


def test_bot_without_profile_or_opening_starts_empty_and_uses_its_own_rules(player, model, app):
    started = start_bot(player, "assistant")  # no onboarding needed
    assert started["opening_message"] is None
    assert started["session"]["bot_id"] == "assistant"
    assert model.calls == []  # this bot does not open the conversation

    session_id = started["session"]["id"]
    model.script(COACH, "Hello there.")
    reply = say(player, session_id, "Hi")
    assert reply.status_code == 201 and reply.json()["reply"]["content"] == "Hello there."

    call = model.calls_for(COACH)[-1]
    assert call["system"] == app.state.bots.get("assistant").prompts.get("rules")
    assert "tennis" not in call["system"].lower()
    assert call["messages"] == [{"role": "user", "content": "Hi"}]


def test_every_bot_is_safety_checked(player, model, app, db):
    session_id = start_bot(player, "assistant")["session"]["id"]
    model.script(SAFETY, "suicide")
    body = say(player, session_id, "a flagged message").json()
    assert body["session_status"] == "flagged"
    assert body["reply"]["content"] == app.state.bots.get("assistant").prompts.get("safety_message")
    assert "988" in body["reply"]["content"]
    assert model.calls_for(COACH) == []
    assert model.calls_for(SAFETY)[0]["system"] == app.state.bots.get("assistant").prompts.get("safety_check")
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary")["n"] == 0  # this bot has no memory


def test_bot_without_memory_ends_without_a_summary_call(player, model, db):
    session_id = start_bot(player, "assistant")["session"]["id"]
    say(player, session_id, "Hi")
    ended = player.post(f"/api/sessions/{session_id}/end").json()
    assert ended["status"] == "ended" and ended["summary_status"] == "done"
    assert model.calls_for(SUMMARY) == []
    player.post("/api/player")  # next visit: nothing to retry
    assert model.calls_for(SUMMARY) == []
    assert player.get("/api/memory").json() == []


def test_unknown_bot_or_task_is_rejected(ready):
    for body in [
        {"bot_id": "nope", "task_type": "chat"},
        {"bot_id": "../tennis", "task_type": "post_loss"},
        {"bot_id": "_shared", "task_type": "chat"},
        {"bot_id": "assistant", "task_type": "post_loss"},
        {"bot_id": "tennis", "task_type": "chat"},
    ]:
        assert ready.post("/api/sessions", json=body).status_code == 422, body


def test_tennis_still_needs_a_profile_and_is_the_default_bot(player):
    assert player.post("/api/sessions", json={"bot_id": "tennis", "task_type": "post_loss"}).status_code == 409
    player.post("/api/profile", json=PROFILE)
    session_id = start(player)  # no bot_id given
    assert player.get(f"/api/sessions/{session_id}").json()["session"]["bot_id"] == "tennis"


@pytest.fixture
def two_memory_bots(settings, tmp_path):
    """Tennis plus a copy of it under another name, so both have memory."""
    bots_dir = tmp_path / "bots"
    shutil.copytree(ROOT / "bots", bots_dir)
    shutil.copytree(bots_dir / "tennis", bots_dir / "golf")
    config = json.loads((bots_dir / "golf" / "bot.json").read_text())
    (bots_dir / "golf" / "bot.json").write_text(json.dumps({**config, "name": "Golf coach", "order": 2}))
    (bots_dir / "golf" / "rules.txt").write_text("You are a golf coach.")
    app = create_app(replace(settings, bots_dir=bots_dir))
    client = TestClient(app)
    client.post("/api/player")
    client.post("/api/profile", json=PROFILE)
    return app, client


def finish(client, model, bot_id, bullet):
    session_id = start_bot(client, bot_id, "post_loss")["session"]["id"]
    model.script(SUMMARY, json.dumps({"summary": [bullet], "result": None}))
    client.post(f"/api/sessions/{session_id}/end")


def test_memory_is_kept_separate_per_bot(two_memory_bots):
    app, client = two_memory_bots
    model = app.state.model
    finish(client, model, "tennis", "Tennis note")
    finish(client, model, "golf", "Golf note")

    start_bot(client, "golf", "post_loss")
    golf_prompt = model.calls_for(COACH)[-1]["system"]
    assert golf_prompt.startswith("You are a golf coach.")
    assert "Golf note" in golf_prompt and "Tennis note" not in golf_prompt
    start_bot(client, "tennis", "post_loss")
    tennis_prompt = model.calls_for(COACH)[-1]["system"]
    assert "Tennis note" in tennis_prompt and "Golf note" not in tennis_prompt

    # Starting the tennis session ended the open golf one, so golf has a second summary.
    golf = [m["text"] for m in client.get("/api/memory?bot_id=golf").json()]
    assert "- Golf note" in golf and "- Tennis note" not in golf
    assert all(m["bot_id"] == "golf" for m in client.get("/api/memory?bot_id=golf").json())
    assert len(client.get("/api/memory").json()) == len(golf) + 1

    assert client.delete("/api/memory?bot_id=golf").status_code == 204
    assert client.get("/api/memory?bot_id=golf").json() == []
    assert "- Tennis note" in [m["text"] for m in client.get("/api/memory?bot_id=tennis").json()]


def test_a_new_bot_folder_appears_without_a_restart(two_memory_bots, tmp_path):
    app, client = two_memory_bots
    folder = tmp_path / "bots" / "pirate"
    folder.mkdir()
    (folder / "bot.json").write_text(json.dumps({"name": "Pirate", "tasks": [{"id": "chat"}]}))
    assert "pirate" not in [bot["id"] for bot in client.get("/api/bots").json()]  # no rules.txt yet
    (folder / "rules.txt").write_text("Talk like a pirate.")
    assert "pirate" in [bot["id"] for bot in client.get("/api/bots").json()]
    session_id = start_bot(client, "pirate")["session"]["id"]
    say(client, session_id, "Ahoy")
    assert app.state.model.calls_for(COACH)[-1]["system"] == "Talk like a pirate."


def test_broken_bot_folder_is_skipped(two_memory_bots, tmp_path):
    _, client = two_memory_bots
    folder = tmp_path / "bots" / "broken"
    folder.mkdir()
    (folder / "bot.json").write_text("{not json")
    (folder / "rules.txt").write_text("x")
    ids = [bot["id"] for bot in client.get("/api/bots").json()]
    assert "broken" not in ids and "tennis" in ids and "_shared" not in ids


def test_reviewer_sees_which_bot_ran_each_session(ready, model, reviewer):
    tennis = start(ready)
    assistant = start_bot(ready, "assistant")["session"]["id"]
    rows = {row["id"]: row for row in reviewer.get("/api/review/sessions").json()}
    assert rows[tennis]["bot_name"] == "Tennis mental performance coach"
    assert rows[tennis]["task_title"] == "Post-loss debrief"
    assert rows[assistant]["bot_name"] == "Plain assistant" and rows[assistant]["task_title"] == "Chat"
    detail = reviewer.get(f"/api/review/sessions/{assistant}").json()["session"]
    assert detail["bot_id"] == "assistant" and detail["bot_name"] == "Plain assistant"


def test_old_database_is_upgraded_and_keeps_its_sessions(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE player (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
        CREATE TABLE session (id TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES player(id),
            task_type TEXT NOT NULL CHECK (task_type IN ('post_loss', 'pre_match', 'nerves')),
            status TEXT NOT NULL CHECK (status IN ('open', 'ended', 'flagged')),
            takeaway TEXT, outcome TEXT,
            summary_status TEXT NOT NULL CHECK (summary_status IN ('pending', 'done', 'failed')),
            summary_attempts INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, ended_at TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session(id),
            sender TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, seq INTEGER NOT NULL);
        INSERT INTO player VALUES ('p1', 'h', 't', 't');
        INSERT INTO session VALUES ('s1', 'p1', 'nerves', 'ended', NULL, 'Box breathing', 'done', 1, 't1', 't2');
        INSERT INTO message VALUES ('m1', 's1', 'bot', 'hello', 't1', 1);
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    session = db.one("SELECT * FROM session")
    assert session["bot_id"] == "tennis" and session["outcome"] == "Box breathing"
    assert session["summary_attempts"] == 1 and session["started_at"] == "t1"
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE session_id = 's1'")["n"] == 1
    # A task type the old table would have refused, with foreign keys still enforced.
    db.run(
        "INSERT INTO session (id, player_id, bot_id, task_type, status, summary_status, started_at)"
        " VALUES ('s2', 'p1', 'assistant', 'chat', 'open', 'pending', 't3')"
    )
    db.run("INSERT INTO message VALUES ('m2', 's2', 'player', 'hi', 't3', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO message VALUES ('m3', 'no-such-session', 'player', 'hi', 't3', 1)")
    Database(path)  # opening it again changes nothing
    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 2
