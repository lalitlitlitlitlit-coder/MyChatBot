"""M3: model client, post-loss debrief, sessions."""
import json
import re
import shutil
from pathlib import Path

import pytest

import app as app_package
from app.config import ROOT
from app.main import create_app
from app.model_client import COACH, SUMMARY, ModelError, Sleep
from tests.conftest import PROFILE, say, start

RETRY = "I couldn't reply just now. Tap to try again."


def summary_json(bullets, result):
    return json.dumps({"summary": bullets, "result": result})


# FR-06
def test_session_needs_a_profile(player):
    assert player.post("/api/sessions", json={"task_type": "post_loss"}).status_code == 409


# FR-09
@pytest.mark.parametrize("task_type", ["post_loss", "pre_match", "nerves"])
def test_each_task_type_starts_a_session(ready, model, task_type):
    model.script(COACH, "Tough one. What happened out there?")
    response = ready.post("/api/sessions", json={"task_type": task_type})
    assert response.status_code == 201
    body = response.json()
    assert body["session"]["task_type"] == task_type
    assert body["session"]["status"] == "open"
    assert body["opening_message"]["sender"] == "bot"
    assert body["opening_message"]["content"] == "Tough one. What happened out there?"


@pytest.mark.parametrize("task_type", ["", "therapy", None])
def test_bad_task_type_is_rejected(ready, task_type):
    assert ready.post("/api/sessions", json={"task_type": task_type}).status_code == 422


# FR-11
def test_prompt_has_task_instructions_profile_and_messages(ready, model, app):
    session_id = start(ready)
    model.script(COACH, "That sounds frustrating.")
    assert say(ready, session_id, "Lost 7-5 6-4 after double-faulting.").status_code == 201
    say(ready, session_id, "I fell apart in the second set.")

    call = model.calls_for(COACH)[-1]
    assert app.state.bots.get("tennis").prompts.get("rules") in call["system"]
    assert app.state.bots.get("tennis").prompts.get("post_loss") in call["system"]
    assert "Maya" in call["system"] and "16" in call["system"]
    assert "national junior" in call["system"]
    assert "Serving when ahead in the set" in call["system"]
    contents = [m["content"] for m in call["messages"]]
    assert "Lost 7-5 6-4 after double-faulting." in contents
    assert "That sounds frustrating." in contents
    assert contents[-1] == "I fell apart in the second set."
    assert call["messages"][-1]["role"] == "user"


# FR-12
def test_opening_prompt_forbids_profile_questions(ready, model):
    start(ready)
    call = model.calls_for(COACH)[0]
    assert len(call["messages"]) == 1
    assert "Do not ask for profile information" in call["messages"][0]["content"]
    assert "Never ask for profile information" in call["system"]


# FR-13
def test_takeaway_is_stored_when_session_ends(ready, model, db):
    session_id = start(ready)
    say(ready, session_id, "Lost 7-5 6-4.")
    model.script(SUMMARY, summary_json(["Lost 7-5 6-4"], "Do my 4-breath reset before every serve at 5-4."))
    response = ready.post(f"/api/sessions/{session_id}/end")
    assert response.status_code == 200
    ended = response.json()
    assert ended["status"] == "ended"
    assert ended["takeaway"] == "Do my 4-breath reset before every serve at 5-4."
    assert ended["outcome"] is None
    assert ended["ended_at"]
    transcript = model.calls_for(SUMMARY)[0]["messages"][0]["content"]
    assert "Session type: post_loss" in transcript and "Player: Lost 7-5 6-4." in transcript


def test_post_loss_prompt_asks_for_calm_then_one_takeaway(app):
    text = app.state.bots.get("tennis").prompts.get("post_loss")
    assert "Calm first" in text
    assert "exactly one concrete takeaway" in text


# E-05
def test_ending_before_a_takeaway_leaves_it_null(ready, model):
    session_id = start(ready)
    model.script(SUMMARY, summary_json(["Session ended before any discussion"], None))
    ended = ready.post(f"/api/sessions/{session_id}/end").json()
    assert ended["takeaway"] is None and ended["summary_status"] == "done"


# FR-16
def test_coach_rules_contain_the_clinical_limits(app):
    rules = app.state.bots.get("tennis").prompts.get("rules")
    assert "not a psychologist, therapist" in rules
    assert "Do not diagnose" in rules
    assert "qualified professional or a trusted adult" in rules


# FR-17, E-12
def test_ended_session_accepts_nothing_more(ready, db):
    session_id = start(ready)
    assert ready.post(f"/api/sessions/{session_id}/end").status_code == 200
    assert say(ready, session_id, "One more thing").status_code == 409
    assert ready.post(f"/api/sessions/{session_id}/end").status_code == 409
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'player'")["n"] == 0


# A-07
def test_starting_a_new_session_ends_the_open_one(ready, db):
    first = start(ready)
    second = start(ready)
    assert ready.get(f"/api/sessions/{first}").json()["session"]["status"] == "ended"
    assert ready.get(f"/api/sessions/{second}").json()["session"]["status"] == "open"
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary WHERE session_id = ?", (first,))["n"] == 1


# E-04
def test_open_session_can_be_reopened(ready, model):
    session_id = start(ready)
    say(ready, session_id, "Lost in three sets.")
    body = ready.get(f"/api/sessions/{session_id}").json()
    assert body["session"]["status"] == "open"
    assert [m["sender"] for m in body["messages"]] == ["bot", "player", "bot"]


# E-06
@pytest.mark.parametrize("content", ["", "   ", "x" * 2001, None, 5])
def test_bad_message_is_rejected_and_not_stored(ready, db, content):
    session_id = start(ready)
    assert say(ready, session_id, content).status_code == 422
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'player'")["n"] == 0


def test_2000_character_message_is_accepted(ready):
    assert say(ready, start(ready), "x" * 2000).status_code == 201


# FR-03, E-10
def test_other_players_session_is_404(ready, new_client):
    session_id = start(ready)
    other = new_client()
    other.post("/api/player")
    other.post("/api/profile", json=PROFILE)
    assert other.get(f"/api/sessions/{session_id}").status_code == 404
    assert say(other, session_id, "hello").status_code == 404
    assert other.post(f"/api/sessions/{session_id}/end").status_code == 404
    assert ready.get("/api/sessions/does-not-exist").status_code == 404


# E-01
def test_model_error_keeps_the_message_and_retry_does_not_duplicate_it(ready, model, db):
    session_id = start(ready)
    model.script(COACH, ModelError("provider down"))
    failed = say(ready, session_id, "Lost 6-0 6-0.")
    assert failed.status_code == 502
    assert failed.json()["detail"] == RETRY
    stored = ready.get(f"/api/sessions/{session_id}").json()["messages"]
    assert [m["sender"] for m in stored] == ["bot", "player"]

    retried = say(ready, session_id, "Lost 6-0 6-0.")
    assert retried.status_code == 201
    stored = ready.get(f"/api/sessions/{session_id}").json()["messages"]
    assert [m["sender"] for m in stored] == ["bot", "player", "bot"]
    assert retried.json()["player_message"]["id"] == stored[1]["id"]


def test_opening_failure_creates_no_session(ready, model, db):
    model.script(COACH, ModelError("provider down"))
    assert ready.post("/api/sessions", json={"task_type": "post_loss"}).status_code == 502
    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 0


# NFR-07
def test_model_timeout_returns_504_and_keeps_the_message(ready, model, db):
    session_id = start(ready)
    model.script(COACH, Sleep(1.0))
    response = say(ready, session_id, "Lost in a tiebreak.")
    assert response.status_code == 504
    assert response.json()["detail"] == RETRY
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'player'")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'bot'")["n"] == 1  # opening only


def test_default_timeout_is_30_seconds():
    from app.config import Settings

    assert Settings("x" * 12, "s").model_timeout_seconds == 30


# NFR-04
def test_only_the_model_client_talks_to_a_provider():
    package = Path(app_package.__file__).parent
    for path in package.rglob("*.py"):
        if path.name == "model_client.py":
            continue
        text = path.read_text()
        imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", text, flags=re.M)
        for module in imports:
            root = module.split(".")[0]
            assert root not in {"urllib", "http", "httpx", "requests", "groq", "openai", "anthropic"}, (
                f"{module} imported in {path.name}"
            )
        assert "groq.com" not in text


# NFR-10
def test_editing_a_prompt_file_changes_the_next_prompt(settings, tmp_path, new_client):
    bots_dir = tmp_path / "bots"
    shutil.copytree(ROOT / "bots", bots_dir)
    from dataclasses import replace

    app = create_app(replace(settings, bots_dir=bots_dir))
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/player")
    client.post("/api/profile", json=PROFILE)
    session_id = start(client)
    (bots_dir / "tennis" / "post_loss.txt").write_text("EDITED BY THE REVIEWER")
    say(client, session_id, "Lost 7-5 6-4.")
    assert "EDITED BY THE REVIEWER" in app.state.model.calls_for(COACH)[-1]["system"]


# FR-10
def test_no_audio_in_the_frontend(anonymous):
    for path in ["/", "/static/app.js", "/review", "/static/review.js"]:
        text = anonymous.get(path).text.lower()
        for marker in ["<audio", "getusermedia", "speechsynthesis", "speechrecognition", "mediarecorder"]:
            assert marker not in text
