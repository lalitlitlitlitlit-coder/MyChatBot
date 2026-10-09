"""Reviewer endpoints, behind the shared access code (FR-04)."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import inspector, sessions
from ..identity import (
    REVIEWER_COOKIE,
    REVIEWER_COOKIE_SECONDS,
    access_code_matches,
    require_reviewer,
    reviewer_cookie_value,
    set_cookie,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/review")


class UnlockBody(BaseModel):
    access_code: str


@router.post("/unlock", status_code=204)
def unlock(body: UnlockBody, request: Request, response: Response):
    settings, limiter = request.app.state.settings, request.app.state.limiter
    ip = request.client.host if request.client else "unknown"
    if limiter.is_locked(ip):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    if not access_code_matches(body.access_code, settings.reviewer_access_code):
        limiter.record_wrong(ip)
        log.info("reviewer unlock failed")
        raise HTTPException(401, "That code isn't right.")
    set_cookie(
        request,
        response,
        REVIEWER_COOKIE,
        reviewer_cookie_value(settings.session_secret),
        REVIEWER_COOKIE_SECONDS,
    )
    log.info("reviewer unlocked")


@router.post("/lock", status_code=204)
def lock(response: Response):
    response.delete_cookie(REVIEWER_COOKIE)


SESSION_LIST = """
    SELECT s.id, s.player_id, substr(s.player_id, 1, 8) AS player_short_id,
           p.display_name, s.bot_id, s.task_type, s.status, s.takeaway, s.outcome,
           s.summary_status, s.started_at, s.ended_at
    FROM session s LEFT JOIN player_profile p ON p.player_id = s.player_id
"""


def with_labels(request, session):
    """Add the chatbot's name and the task's title, falling back to their ids."""
    bot = request.app.state.bots.get(session["bot_id"])
    task = bot.task(session["task_type"]) if bot else None
    session["bot_name"] = bot.name if bot else session["bot_id"]
    session["task_title"] = task["title"] if task else session["task_type"]
    return session


@router.get("/sessions", dependencies=[Depends(require_reviewer)])
def list_sessions(request: Request):
    rows = request.app.state.db.all(SESSION_LIST + " ORDER BY s.started_at DESC, s.rowid DESC")
    return [with_labels(request, row) for row in rows]


@router.get("/sessions/{session_id}", dependencies=[Depends(require_reviewer)])
def read_session(session_id: str, request: Request):
    db = request.app.state.db
    session = db.one(SESSION_LIST + " WHERE s.id = ?", (session_id,))
    if not session:
        raise HTTPException(404, "Session not found.")
    return {
        "session": with_labels(request, session),
        "messages": sessions.get_messages(db, session_id),
        "safety_events": db.all(
            "SELECT id, session_id, message_id, category, created_at FROM safety_event"
            " WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ),
    }


@router.get("/sessions/{session_id}/calls", dependencies=[Depends(require_reviewer)])
def read_model_calls(session_id: str, request: Request):
    """Every model call made for a session: what was sent and what came back."""
    db = request.app.state.db
    if not sessions.get_session(db, session_id):
        raise HTTPException(404, "Session not found.")
    return inspector.calls_for_session(db, session_id)
