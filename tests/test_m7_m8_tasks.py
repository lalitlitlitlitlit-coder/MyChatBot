"""M7 and M8: pre-match focus routine and handling nerves."""
import json

import pytest

from app.model_client import COACH, SUMMARY
from tests.conftest import say, start

ROUTINE = "1) Three slow breaths; 2) Cue word 'smooth'; 3) Picture the first service game"


def end_with_result(client, model, task_type, result):
    session_id = start(client, task_type)
    say(client, session_id, "Match is in an hour.")
    model.script(SUMMARY, json.dumps({"summary": ["Match in an hour"], "result": result}))
    response = client.post(f"/api/sessions/{session_id}/end")
    assert response.status_code == 200
    return session_id, response.json()


# FR-14
def test_pre_match_routine_is_stored_as_outcome(ready, model, reviewer):
    session_id, ended = end_with_result(ready, model, "pre_match", ROUTINE)
    assert ended["outcome"] == ROUTINE
    assert ended["takeaway"] is None
    assert reviewer.get(f"/api/review/sessions/{session_id}").json()["session"]["outcome"] == ROUTINE
    assert "Session type: pre_match" in model.calls_for(SUMMARY)[-1]["messages"][0]["content"]


# FR-15
def test_nerves_technique_is_stored_as_outcome(ready, model, reviewer):
    session_id, ended = end_with_result(ready, model, "nerves", "Box breathing")
    assert ended["outcome"] == "Box breathing"
    assert ended["takeaway"] is None
    assert reviewer.get("/api/review/sessions").json()[0]["outcome"] == "Box breathing"


@pytest.mark.parametrize("task_type", ["pre_match", "nerves"])
def test_ending_early_leaves_outcome_null(ready, model, task_type):
    _, ended = end_with_result(ready, model, task_type, None)
    assert ended["outcome"] is None and ended["status"] == "ended"


@pytest.mark.parametrize(
    "task_type,phrases",
    [
        ("pre_match", ["short focus routine", "3 or 4 steps", "Your routine:"]),
        ("nerves", ["exactly one calming technique", "how they feel now"]),
    ],
)
def test_task_prompt_is_used_and_states_the_goal(ready, model, app, task_type, phrases):
    start(ready, task_type)
    task_prompt = app.state.bots.get("tennis").prompts.get(task_type)
    assert task_prompt in model.calls_for(COACH)[-1]["system"]
    for phrase in phrases:
        assert phrase in task_prompt


def test_summary_prompt_defines_the_result_for_each_task(app):
    text = app.state.bots.get("tennis").prompts.get("summary")
    for task_type in ["post_loss", "pre_match", "nerves"]:
        assert f"- {task_type}:" in text


def test_all_three_tasks_are_offered(player):
    tennis = next(bot for bot in player.get("/api/bots").json() if bot["id"] == "tennis")
    assert [task["id"] for task in tennis["tasks"]] == ["post_loss", "pre_match", "nerves"]
    assert [task["button"] for task in tennis["tasks"]] == [
        "I just lost a match", "Prepare for a match", "I'm feeling nervous",
    ]
