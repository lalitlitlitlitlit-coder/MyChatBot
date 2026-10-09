"""Tester endpoints: everything a player's browser calls."""
import logging
import sqlite3
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from .. import inspector, sessions
from ..db import now
from ..identity import create_player, find_player, require_player
from ..model_client import ModelError, ModelTimeout
from ..sessions import get_profile

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def has_profile(db, player_id):
    return get_profile(db, player_id) is not None


@router.post("/player")
def identify_player(request: Request, response: Response, background: BackgroundTasks):
    db = request.app.state.db
    player = find_player(request)
    if player:
        # A new visit: give any failed summary its one retry, without delaying the page.
        background.add_task(sessions.retry_failed_summaries, request.app.state, player["id"])
        return {"player_id": player["id"], "has_profile": has_profile(db, player["id"])}
    try:
        player = create_player(request, response)
    except Exception:
        log.exception("player creation failed")
        raise HTTPException(500, "Something went wrong. Refresh the page.")
    response.status_code = 201
    return {"player_id": player["id"], "has_profile": False}


# --- Profile (onboarding) -----------------------------------------------------

PlayingLevel = Literal[
    "beginner",
    "intermediate",
    "advanced",
    "national_junior",
    "international_junior",
    "college",
    "professional",
]


class ProfileBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    age: Annotated[int, Field(strict=True, ge=1, le=99)]
    playing_level: PlayingLevel
    pressure_points: Optional[str] = Field(None, max_length=500)

    @field_validator("display_name", "pressure_points", mode="before")
    @classmethod
    def strip(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.get("/profile")
def read_profile(request: Request, player: dict = Depends(require_player)):
    profile = get_profile(request.app.state.db, player["id"])
    if not profile:
        raise HTTPException(404, "No profile yet.")
    return profile


@router.post("/profile", status_code=201)
def create_profile(body: ProfileBody, request: Request, player: dict = Depends(require_player)):
    db = request.app.state.db
    try:
        db.run(
            "INSERT INTO player_profile (player_id, display_name, age, playing_level,"
            " pressure_points, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (player["id"], body.display_name, body.age, body.playing_level,
             body.pressure_points or None, now()),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A profile already exists for this player.")
    log.info("profile created player=%s", player["id"])
    return get_profile(db, player["id"])


# --- Sessions and conversation -----------------------------------------------

RETRY_MESSAGE = "I couldn't reply just now. Tap to try again."


DEFAULT_BOT = "tennis"


class SessionBody(BaseModel):
    bot_id: str = DEFAULT_BOT
    task_type: str


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content", mode="before")
    @classmethod
    def strip(cls, value):
        return value.strip() if isinstance(value, str) else value


def model_failure(exc):
    return HTTPException(504 if isinstance(exc, ModelTimeout) else 502, RETRY_MESSAGE)


def own_session(request, player, session_id):
    session = sessions.get_session(request.app.state.db, session_id, player["id"])
    if not session:
        raise HTTPException(404, "Session not found.")
    return session


@router.get("/bots")
def list_bots(request: Request, player: dict = Depends(require_player)):
    """The chatbots to choose from on the main page."""
    return [bot.public() for bot in request.app.state.bots.all()]


@router.post("/sessions", status_code=201)
def start_session(body: SessionBody, request: Request, player: dict = Depends(require_player)):
    state = request.app.state
    bot = state.bots.get(body.bot_id)
    if not bot:
        raise HTTPException(422, "Unknown chatbot.")
    if not bot.task(body.task_type):
        raise HTTPException(422, "Unknown task for this chatbot.")
    profile = get_profile(state.db, player["id"])
    if bot.needs_profile and not profile:
        raise HTTPException(409, "Finish onboarding before starting a session.")
    try:
        session, opening = sessions.start_session(
            state, player["id"], profile, bot, body.task_type
        )
    except ModelError as exc:
        raise model_failure(exc)
    return {"session": session, "opening_message": opening}


@router.get("/sessions/{session_id}")
def read_session(session_id: str, request: Request, player: dict = Depends(require_player)):
    session = own_session(request, player, session_id)
    return {"session": session, "messages": sessions.get_messages(request.app.state.db, session_id)}


@router.get("/sessions/{session_id}/calls")
def read_model_calls(session_id: str, request: Request, player: dict = Depends(require_player)):
    """The inspector panel: every model call made for one of the player's own sessions."""
    own_session(request, player, session_id)
    return inspector.calls_for_session(request.app.state.db, session_id)


@router.post("/sessions/{session_id}/messages", status_code=201)
def send_message(
    session_id: str, body: MessageBody, request: Request, player: dict = Depends(require_player)
):
    state = request.app.state
    session = own_session(request, player, session_id)
    if session["status"] != "open":
        raise HTTPException(409, "This session is closed.")
    bot = state.bots.get(session["bot_id"])
    if not bot:
        raise HTTPException(409, "This chatbot is no longer available.")
    try:
        player_message, reply, status = sessions.handle_message(
            state, session, bot, get_profile(state.db, player["id"]), body.content
        )
    except ModelError as exc:
        raise model_failure(exc)
    return {"player_message": player_message, "reply": reply, "session_status": status}


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, request: Request, player: dict = Depends(require_player)):
    session = own_session(request, player, session_id)
    try:
        return sessions.end_session(request.app.state, session)
    except sessions.SessionNotOpen:
        raise HTTPException(409, "This session has already ended.")


# --- Memory ---------------------------------------------------------------------


@router.get("/memory")
def list_memory(
    request: Request, bot_id: Optional[str] = None, player: dict = Depends(require_player)
):
    """The player's summaries, newest first; with `bot_id`, only that chatbot's."""
    return request.app.state.db.all(
        "SELECT m.id, m.session_id, m.text, m.created_at, s.task_type, s.bot_id"
        " FROM memory_summary m JOIN session s ON s.id = m.session_id"
        " WHERE m.player_id = ? AND (? IS NULL OR s.bot_id = ?)"
        " ORDER BY m.created_at DESC, m.rowid DESC",
        (player["id"], bot_id, bot_id),
    )


@router.delete("/memory/{summary_id}", status_code=204)
def delete_memory(summary_id: str, request: Request, player: dict = Depends(require_player)):
    # Hard delete (FR-25). Sessions, messages, and safety events are kept (FR-26).
    deleted = request.app.state.db.run(
        "DELETE FROM memory_summary WHERE id = ? AND player_id = ?", (summary_id, player["id"])
    )
    if not deleted:
        raise HTTPException(404, "Summary not found.")
    log.info("memory summary deleted player=%s", player["id"])


@router.delete("/memory", status_code=204)
def delete_all_memory(
    request: Request, bot_id: Optional[str] = None, player: dict = Depends(require_player)
):
    """Delete all of the player's summaries; with `bot_id`, only that chatbot's."""
    request.app.state.db.run(
        "DELETE FROM memory_summary WHERE player_id = ? AND (? IS NULL OR session_id IN"
        " (SELECT id FROM session WHERE bot_id = ?))",
        (player["id"], bot_id, bot_id),
    )
    log.info("all memory deleted player=%s", player["id"])
