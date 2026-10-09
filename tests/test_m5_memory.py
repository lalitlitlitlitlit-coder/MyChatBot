"""M5: memory summaries."""
import json

from app.model_client import COACH, SAFETY, SUMMARY, ModelError
from tests.conftest import PROFILE, say, start


def summary_json(bullets, result=None):
    return json.dumps({"summary": bullets, "result": result})


def finish(client, model, bullets, task_type="post_loss"):
    """Run one whole session whose summary is `bullets`; return the session id."""
    session_id = start(client, task_type)
    model.script(SUMMARY, summary_json(bullets))
    assert client.post(f"/api/sessions/{session_id}/end").status_code == 200
    return session_id


def last_coach_prompt(model):
    return model.calls_for(COACH)[-1]["system"]


# FR-21
def test_ending_a_session_stores_one_summary(ready, model, db):
    finish(ready, model, ["Lost 7-5 6-4", "Box breathing helped"])
    rows = db.all("SELECT * FROM memory_summary")
    assert len(rows) == 1
    assert rows[0]["text"] == "- Lost 7-5 6-4\n- Box breathing helped"


def test_summary_is_cut_to_five_bullets(ready, model):
    finish(ready, model, [f"Point {n}" for n in range(1, 8)])
    text = ready.get("/api/memory").json()[0]["text"]
    assert text.splitlines() == [f"- Point {n}" for n in range(1, 6)]


# FR-22
def test_summary_prompt_requires_neutral_language(app):
    text = app.state.bots.get("tennis").prompts.get("summary")
    assert "Neutral and factual" in text
    assert "No judgments about the player's character" in text
    assert "No mental health labels" in text


# FR-11
def test_summaries_are_given_to_the_bot(ready, model):
    finish(ready, model, ["Double-faults when serving for the set"])
    start(ready)
    assert "Double-faults when serving for the set" in last_coach_prompt(model)


# A-11
def test_bot_gets_at_most_the_ten_newest_summaries(ready, model):
    for n in range(1, 13):
        finish(ready, model, [f"Session note {n:02d}"])
    start(ready)
    prompt = last_coach_prompt(model)
    assert "Session note 01" not in prompt and "Session note 02" not in prompt
    assert all(f"Session note {n:02d}" in prompt for n in range(3, 13))
    assert len(ready.get("/api/memory").json()) == 12


# FR-24
def test_memory_lists_only_own_summaries_newest_first(ready, model, new_client):
    finish(ready, model, ["First session"])
    finish(ready, model, ["Second session"], "nerves")
    other = new_client()
    other.post("/api/player")
    other.post("/api/profile", json=PROFILE)
    finish(other, model, ["Someone else's session"])

    mine = ready.get("/api/memory").json()
    assert [m["text"] for m in mine] == ["- Second session", "- First session"]
    assert [m["task_type"] for m in mine] == ["nerves", "post_loss"]
    assert all(m["created_at"] for m in mine)


# FR-25, E-09
def test_deleting_one_summary_removes_it_from_db_and_prompt(ready, model, db):
    finish(ready, model, ["Keep this note"])
    finish(ready, model, ["Delete this note"])
    target = next(m for m in ready.get("/api/memory").json() if "Delete" in m["text"])

    assert ready.delete(f"/api/memory/{target['id']}").status_code == 204
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary WHERE id = ?", (target["id"],))["n"] == 0
    assert [m["text"] for m in ready.get("/api/memory").json()] == ["- Keep this note"]
    start(ready)
    prompt = last_coach_prompt(model)
    assert "Keep this note" in prompt and "Delete this note" not in prompt
    assert ready.delete(f"/api/memory/{target['id']}").status_code == 404


def test_deleting_all_leaves_the_bot_with_no_memory(ready, model, db):
    finish(ready, model, ["Note one"])
    finish(ready, model, ["Note two"])
    assert ready.delete("/api/memory").status_code == 204
    assert ready.get("/api/memory").json() == []
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary")["n"] == 0
    start(ready)
    prompt = last_coach_prompt(model)
    assert "Note one" not in prompt and "Note two" not in prompt
    assert "Treat this as your first session" in prompt
    assert "Maya" in prompt  # the profile is still used


def test_cannot_delete_another_players_summary(ready, model, new_client, db):
    finish(ready, model, ["Mine"])
    summary_id = ready.get("/api/memory").json()[0]["id"]
    other = new_client()
    other.post("/api/player")
    assert other.delete(f"/api/memory/{summary_id}").status_code == 404
    assert other.delete("/api/memory").status_code == 204
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary")["n"] == 1


def test_deleted_summary_is_not_regenerated_on_next_visit(ready, model, db):
    finish(ready, model, ["Note"])
    ready.delete("/api/memory")
    ready.post("/api/player")
    assert db.one("SELECT COUNT(*) AS n FROM memory_summary")["n"] == 0


# FR-26, FR-29
def test_deleting_memory_keeps_sessions_messages_and_safety_events(ready, model, db, reviewer):
    ended = finish(ready, model, ["Note"])
    flagged = start(ready)
    model.script(SAFETY, "abuse")
    say(ready, flagged, "flagged message")
    assert ready.delete("/api/memory").status_code == 204

    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM message")["n"] == 4
    assert db.one("SELECT COUNT(*) AS n FROM safety_event")["n"] == 1
    listed = {s["id"] for s in reviewer.get("/api/review/sessions").json()}
    assert listed == {ended, flagged}


def test_memory_screen_has_both_notes(anonymous):
    html = anonymous.get("/").text
    assert "Session transcripts are kept for review" in html
    assert "Memory is tied to this browser" in html
    assert "Clearing browser data or switching device starts fresh" in html


def test_only_memory_deletion_removes_data():
    from pathlib import Path

    import app as app_package

    deletes = []
    for path in Path(app_package.__file__).parent.rglob("*.py"):
        deletes += [line.strip() for line in path.read_text().splitlines() if "DELETE FROM" in line]
    assert len(deletes) == 2
    assert all("DELETE FROM memory_summary" in line for line in deletes)


def test_new_cookie_is_a_new_player_and_old_sessions_stay(ready, model, new_client, reviewer, db):
    old_session = finish(ready, model, ["Note"])
    fresh = new_client()
    created = fresh.post("/api/player")
    assert created.status_code == 201 and created.json()["has_profile"] is False
    assert fresh.get("/api/memory").json() == []
    assert old_session in {s["id"] for s in reviewer.get("/api/review/sessions").json()}
    assert db.one("SELECT COUNT(*) AS n FROM player")["n"] == 2


# FR-23
def test_flagged_session_summary_reaches_memory_without_details(ready, model):
    session_id = start(ready)
    model.script(SAFETY, "suicide")
    say(ready, session_id, "an alarming and specific sentence")
    memory = ready.get("/api/memory").json()
    assert [m["text"] for m in memory] == ["- Session ended early."]
    start(ready)
    assert "alarming" not in last_coach_prompt(model)


# E-03
def test_failed_summary_does_not_block_ending_and_is_retried_once(ready, model, db):
    session_id = start(ready)
    say(ready, session_id, "Lost 7-5 6-4.")
    model.script(SUMMARY, ModelError("down"))
    ended = ready.post(f"/api/sessions/{session_id}/end")
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["summary_status"] == "failed"
    assert ready.get("/api/memory").json() == []

    model.script(SUMMARY, summary_json(["Lost 7-5 6-4"], "Reset before serving at 5-4."))
    ready.post("/api/player")  # the player's next visit
    session = ready.get(f"/api/sessions/{session_id}").json()["session"]
    assert session["summary_status"] == "done"
    assert session["takeaway"] == "Reset before serving at 5-4."
    assert [m["text"] for m in ready.get("/api/memory").json()] == ["- Lost 7-5 6-4"]


def test_summary_that_fails_twice_stays_failed(ready, model, db):
    session_id = start(ready)
    model.script(SUMMARY, ModelError("down"), "not json at all")
    ready.post(f"/api/sessions/{session_id}/end")
    ready.post("/api/player")
    calls = len(model.calls_for(SUMMARY))
    assert calls == 2
    ready.post("/api/player")
    ready.post("/api/player")
    assert len(model.calls_for(SUMMARY)) == calls  # no third attempt
    assert ready.get(f"/api/sessions/{session_id}").json()["session"]["summary_status"] == "failed"
    assert ready.get("/api/memory").json() == []
