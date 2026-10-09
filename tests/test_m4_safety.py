"""M4: safety floor."""
import pytest

from app.model_client import COACH, SAFETY, ModelError, Sleep
from app.safety import CATEGORIES
from tests.conftest import say, start

RETRY = "I couldn't reply just now. Tap to try again."


# FR-18, FR-19, E-07
@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("task_type", ["post_loss", "pre_match", "nerves"])
def test_flagged_message_gets_the_fixed_message_and_closes_the_session(
    ready, model, db, app, category, task_type
):
    session_id = start(ready, task_type)
    coach_calls = len(model.calls_for(COACH))
    model.script(SAFETY, category)

    response = say(ready, session_id, "a message the safety check flags")
    assert response.status_code == 201
    body = response.json()
    assert body["session_status"] == "flagged"
    assert body["reply"]["sender"] == "system"
    assert body["reply"]["content"] == app.state.bots.get("tennis").prompts.get("safety_message")
    assert "988" in body["reply"]["content"]
    assert len(model.calls_for(COACH)) == coach_calls  # no coaching reply was generated

    session = ready.get(f"/api/sessions/{session_id}").json()
    assert session["session"]["status"] == "flagged"
    assert [m["sender"] for m in session["messages"]] == ["bot", "player", "system"]
    event = db.one("SELECT * FROM safety_event")
    assert event["category"] == category
    assert event["session_id"] == session_id
    assert event["message_id"] == body["player_message"]["id"]

    assert say(ready, session_id, "are you still there?").status_code == 409
    assert ready.post(f"/api/sessions/{session_id}/end").status_code == 409


def test_safety_message_says_what_the_spec_requires(app):
    text = app.state.bots.get("tennis").prompts.get("safety_message")
    assert "can't help with this" in text
    assert "trusted adult or a professional now" in text
    assert "988 Suicide & Crisis Lifeline" in text
    assert "call or text" in text


def test_safety_check_runs_first_and_safe_messages_get_coaching(ready, model):
    session_id = start(ready)
    model.calls.clear()
    assert say(ready, session_id, "I lost 6-4 6-4 and I'm annoyed.").json()["reply"]["sender"] == "bot"
    assert [call["purpose"] for call in model.calls] == [SAFETY, COACH]
    assert "I lost 6-4 6-4 and I'm annoyed." in model.calls[0]["messages"][0]["content"]


def test_safety_check_sees_recent_context(ready, model):
    session_id = start(ready)
    say(ready, session_id, "My coach keeps me after practice alone.")
    say(ready, session_id, "He says not to tell anyone.")
    request = model.calls_for(SAFETY)[-1]["messages"][0]["content"]
    assert "Player: My coach keeps me after practice alone." in request
    assert request.endswith("Latest player message:\nHe says not to tell anyone.")


# FR-20, E-02
@pytest.mark.parametrize(
    "failure,status",
    [(ModelError("down"), 502), (Sleep(1.0), 504), ("maybe?", 502), ("none, probably", 502)],
)
def test_failed_safety_check_fails_closed(ready, model, db, failure, status):
    session_id = start(ready)
    coach_calls = len(model.calls_for(COACH))
    model.script(SAFETY, failure)
    response = say(ready, session_id, "Lost 6-2 6-2.")
    assert response.status_code == status
    assert response.json()["detail"] == RETRY
    assert len(model.calls_for(COACH)) == coach_calls
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'player'")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM message WHERE sender = 'bot'")["n"] == 1  # opening only
    assert ready.get(f"/api/sessions/{session_id}").json()["session"]["status"] == "open"


def test_retry_after_failed_safety_check_is_checked_again(ready, model):
    session_id = start(ready)
    model.script(SAFETY, ModelError("down"), "suicide")
    assert say(ready, session_id, "same message").status_code == 502
    retried = say(ready, session_id, "same message")
    assert retried.json()["session_status"] == "flagged"


# FR-23
def test_flagged_session_summary_has_no_details(ready, model, db):
    session_id = start(ready)
    model.script(SAFETY, "self_harm")
    say(ready, session_id, "a very specific flagged sentence")
    summary = db.one("SELECT * FROM memory_summary WHERE session_id = ?", (session_id,))
    assert summary["text"] == "- Session ended early."
    assert "specific" not in summary["text"]
    assert db.one("SELECT summary_status FROM session")["summary_status"] == "done"
