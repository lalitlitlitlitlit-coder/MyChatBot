"""M6: reviewer screens, and what the logs may contain."""
import json
import logging

from app.identity import PLAYER_COOKIE
from app.model_client import COACH, SAFETY, SUMMARY
from tests.conftest import ACCESS_CODE, PROFILE, say, start


def onboard(client, name):
    client.post("/api/player")
    client.post("/api/profile", json={**PROFILE, "display_name": name})
    return client


# FR-27
def test_review_list_shows_sessions_from_all_players(new_client, model, reviewer):
    maya, leo = onboard(new_client(), "Maya"), onboard(new_client(), "Leo")
    first = start(maya)
    model.script(SUMMARY, json.dumps({"summary": ["Lost 7-5 6-4"], "result": "Reset before serving at 5-4."}))
    maya.post(f"/api/sessions/{first}/end")
    second = start(leo, "nerves")

    listed = reviewer.get("/api/review/sessions").json()
    assert [s["id"] for s in listed] == [second, first]  # newest first
    leo_row, maya_row = listed
    assert maya_row["display_name"] == "Maya" and leo_row["display_name"] == "Leo"
    assert maya_row["player_short_id"] == maya_row["player_id"][:8]
    assert len(maya_row["player_short_id"]) == 8
    assert maya_row["task_type"] == "post_loss" and leo_row["task_type"] == "nerves"
    assert maya_row["status"] == "ended" and leo_row["status"] == "open"
    assert maya_row["takeaway"] == "Reset before serving at 5-4."
    assert maya_row["started_at"] and "outcome" in leo_row


# FR-28
def test_review_detail_has_every_message_in_order_and_safety_events(ready, model, reviewer):
    session_id = start(ready)
    model.script(COACH, "That stings. What happened in the second set?")
    say(ready, session_id, "Lost 7-5 6-4.")
    model.script(SAFETY, "self_harm")
    flagged = say(ready, session_id, "a flagged message").json()

    detail = reviewer.get(f"/api/review/sessions/{session_id}").json()
    assert detail["session"]["status"] == "flagged"
    assert detail["session"]["display_name"] == "Maya"
    assert [m["sender"] for m in detail["messages"]] == ["bot", "player", "bot", "player", "system"]
    assert [m["content"] for m in detail["messages"]][1:4] == [
        "Lost 7-5 6-4.",
        "That stings. What happened in the second set?",
        "a flagged message",
    ]
    assert len(detail["safety_events"]) == 1
    event = detail["safety_events"][0]
    assert event["category"] == "self_harm"
    assert event["message_id"] == flagged["player_message"]["id"]


def test_review_detail_unknown_session_is_404(reviewer):
    response = reviewer.get("/api/review/sessions/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found."


# NFR-06
def test_logs_hold_no_content_tokens_or_access_code(new_client, model, caplog):
    secret_words = {
        "profile name": "Zephyrine",
        "pressure points": "quokka-pressure-point",
        "player message": "wombat-message-text",
        "flagged message": "narwhal-flagged-text",
        "bot reply": "axolotl-reply-text",
        "summary": "capybara-summary-text",
        "takeaway": "ocelot-takeaway-text",
        "wrong code": "pangolin-wrong-code",
    }
    with caplog.at_level(logging.DEBUG):
        client = new_client()
        client.post("/api/player")
        client.post(
            "/api/profile",
            json={**PROFILE, "display_name": secret_words["profile name"],
                  "pressure_points": secret_words["pressure points"]},
        )
        session_id = start(client)
        model.script(COACH, secret_words["bot reply"])
        say(client, session_id, secret_words["player message"])
        model.script(SUMMARY, json.dumps({"summary": [secret_words["summary"]], "result": secret_words["takeaway"]}))
        client.post(f"/api/sessions/{session_id}/end")

        flagged = start(client, "nerves")
        model.script(SAFETY, "abuse")
        say(client, flagged, secret_words["flagged message"])
        client.delete("/api/memory")

        reviewer = new_client()
        reviewer.post("/api/review/unlock", json={"access_code": secret_words["wrong code"]})
        reviewer.post("/api/review/unlock", json={"access_code": ACCESS_CODE})
        reviewer.get(f"/api/review/sessions/{session_id}")

    logs = caplog.text
    assert "session started" in logs and "session flagged" in logs and "reviewer unlocked" in logs
    for what, word in secret_words.items():
        assert word not in logs, f"{what} found in logs"
    assert ACCESS_CODE not in logs
    assert client.cookies[PLAYER_COOKIE] not in logs
