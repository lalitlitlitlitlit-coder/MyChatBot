"""Model-call inspector for the reviewer (added after the spec, at the owner's request)."""
import json

from app.model_client import COACH, SAFETY, SUMMARY, ModelError, Sleep
from tests.conftest import PROFILE, say, start


def calls(reviewer, session_id):
    response = reviewer.get(f"/api/review/sessions/{session_id}/calls")
    assert response.status_code == 200
    return response.json()


def test_every_model_call_of_a_session_is_recorded_in_order(ready, model, reviewer, app):
    session_id = start(ready)
    model.script(COACH, "That stings. What happened?")
    say(ready, session_id, "Lost 7-5 6-4.")
    model.script(SUMMARY, json.dumps({"summary": ["Lost 7-5 6-4"], "result": "Reset at 5-4."}))
    ready.post(f"/api/sessions/{session_id}/end")

    recorded = calls(reviewer, session_id)
    assert [c["purpose"] for c in recorded] == [COACH, SAFETY, COACH, SUMMARY]
    opening, safety, reply, summary = recorded
    assert all(c["model"] == "fake" and c["error"] is None and c["duration_ms"] >= 0 for c in recorded)

    assert app.state.bots.get("tennis").prompts.get("post_loss") in opening["system"]
    assert opening["messages"] == [{"role": "user", "content": app.state.bots.get("tennis").prompts.get("opening")}]
    assert safety["system"] == app.state.bots.get("tennis").prompts.get("safety_check")
    assert "Lost 7-5 6-4." in safety["messages"][0]["content"]
    assert safety["response"] == "none"
    assert "Maya" in reply["system"]
    assert reply["messages"][-1] == {"role": "user", "content": "Lost 7-5 6-4."}
    assert reply["response"] == "That stings. What happened?"
    assert json.loads(summary["response"])["result"] == "Reset at 5-4."


def test_failed_and_timed_out_calls_are_recorded(ready, model, reviewer):
    session_id = start(ready)
    model.script(SAFETY, ModelError("Groq returned HTTP 429"))
    assert say(ready, session_id, "first try").status_code == 502
    model.script(COACH, Sleep(1.0))
    assert say(ready, session_id, "first try").status_code == 504

    recorded = calls(reviewer, session_id)[1:]  # skip the opening
    assert [(c["purpose"], c["response"] is None) for c in recorded] == [
        (SAFETY, True), (SAFETY, False), (COACH, True),
    ]
    assert recorded[0]["error"] == "Groq returned HTTP 429"
    assert "too long" in recorded[2]["error"]


def test_flagged_message_records_the_safety_call_and_no_coaching_call(ready, model, reviewer):
    session_id = start(ready)
    model.script(SAFETY, "abuse")
    say(ready, session_id, "a flagged message")
    recorded = calls(reviewer, session_id)
    assert [c["purpose"] for c in recorded] == [COACH, SAFETY]
    assert recorded[-1]["response"] == "abuse"


def test_failed_opening_records_nothing(ready, model, db):
    model.script(COACH, ModelError("down"))
    assert ready.post("/api/sessions", json={"task_type": "post_loss"}).status_code == 502
    assert db.one("SELECT COUNT(*) AS n FROM model_call")["n"] == 0


def test_token_usage_is_stored_when_the_provider_reports_it(ready, model, reviewer, monkeypatch):
    monkeypatch.setattr(
        model, "_default", lambda purpose, messages: ("Hello.", {"prompt_tokens": 812, "completion_tokens": 9})
    )
    session_id = start(ready)
    opening = calls(reviewer, session_id)[0]
    assert opening["prompt_tokens"] == 812 and opening["completion_tokens"] == 9


def test_review_calls_need_the_reviewer_cookie(ready, anonymous, reviewer):
    session_id = start(ready)
    assert ready.get(f"/api/review/sessions/{session_id}/calls").status_code == 401
    assert anonymous.get(f"/api/review/sessions/{session_id}/calls").status_code == 401
    assert reviewer.get("/api/review/sessions/nope/calls").status_code == 404


def test_player_can_inspect_only_their_own_sessions(ready, model, new_client, anonymous, reviewer):
    session_id = start(ready)
    say(ready, session_id, "Lost 7-5 6-4.")
    mine = ready.get(f"/api/sessions/{session_id}/calls")
    assert mine.status_code == 200
    assert [c["purpose"] for c in mine.json()] == [COACH, SAFETY, COACH]
    assert mine.json() == calls(reviewer, session_id)

    other = new_client()
    other.post("/api/player")
    assert other.get(f"/api/sessions/{session_id}/calls").status_code == 404
    assert anonymous.get(f"/api/sessions/{session_id}/calls").status_code == 401
    assert ready.get("/api/sessions/nope/calls").status_code == 404


def test_chat_page_has_the_inspector_panel_and_toggle(anonymous):
    html = anonymous.get("/").text
    assert 'id="inspector-toggle"' in html and 'id="inspector"' in html
    assert "/static/calls.js" in html and "/static/calls.js" in anonymous.get("/review").text


def test_calls_are_kept_per_session(new_client, model, reviewer):
    a, b = new_client(), new_client()
    for client in (a, b):
        client.post("/api/player")
        client.post("/api/profile", json=PROFILE)
    first, second = start(a), start(b, "nerves")
    say(a, first, "only in the first session")
    assert len(calls(reviewer, first)) == 3
    assert len(calls(reviewer, second)) == 1
