"""Session lifecycle: start, exchange messages, end, summarize."""
import logging
import sqlite3

from . import conversation, safety, summarizer
from .db import new_id, now
from .inspector import RecordedModel
from .model_client import ModelError

log = logging.getLogger(__name__)

MAX_SUMMARIES_IN_PROMPT = 10
MAX_SUMMARY_ATTEMPTS = 2  # the first try at session end, then one retry (E-03)
SESSION_COLUMNS = (
    "id, player_id, bot_id, task_type, status, takeaway, outcome, summary_status,"
    " started_at, ended_at"
)


class SessionNotOpen(Exception):
    """The session is ended or flagged and accepts no more changes."""


def get_profile(db, player_id):
    return db.one("SELECT * FROM player_profile WHERE player_id = ?", (player_id,))


def get_session(db, session_id, player_id=None):
    """A session by id; with `player_id`, only if it belongs to that player (FR-03)."""
    session = db.one(f"SELECT {SESSION_COLUMNS} FROM session WHERE id = ?", (session_id,))
    if session and player_id is not None and session["player_id"] != player_id:
        return None
    return session


def get_messages(db, session_id):
    return db.all(
        "SELECT id, session_id, sender, content, created_at FROM message"
        " WHERE session_id = ? ORDER BY seq",
        (session_id,),
    )


def add_message(conn, session_id, sender, content):
    message = {
        "id": new_id(),
        "session_id": session_id,
        "sender": sender,
        "content": content,
        "created_at": now(),
    }
    conn.execute(
        "INSERT INTO message (id, session_id, sender, content, created_at, seq) VALUES"
        " (:id, :session_id, :sender, :content, :created_at,"
        " (SELECT COALESCE(MAX(seq), 0) + 1 FROM message WHERE session_id = :session_id))",
        message,
    )
    return message


def recent_summaries(db, player_id, bot):
    """What the bot remembers: its own newest non-deleted summaries (FR-11)."""
    if not bot.has_memory:
        return []
    return db.all(
        "SELECT m.id, m.session_id, m.text, m.created_at, s.task_type"
        " FROM memory_summary m JOIN session s ON s.id = m.session_id"
        " WHERE m.player_id = ? AND s.bot_id = ?"
        " ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
        (player_id, bot.id, MAX_SUMMARIES_IN_PROMPT),
    )


# --- Start ---------------------------------------------------------------------


def start_session(state, player_id, profile, bot, task_type):
    """Create a session and, for bots that open the conversation, its opening message.

    Returns (session, opening_message or None). Raises ModelError if the model fails.
    """
    db = state.db
    # One open session per player: starting a new one ends the old one first.
    for old in db.all(
        f"SELECT {SESSION_COLUMNS} FROM session WHERE player_id = ? AND status = 'open'",
        (player_id,),
    ):
        try:
            end_session(state, old)
        except SessionNotOpen:
            pass

    session_id = new_id()
    model = RecordedModel(state, session_id, defer=True)
    opening = None
    if bot.has_opening:
        opening = conversation.coach_reply(
            model, bot, task_type, profile, recent_summaries(db, player_id, bot), []
        )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO session (id, player_id, bot_id, task_type, status, summary_status,"
            " started_at) VALUES (?, ?, ?, ?, 'open', 'pending', ?)",
            (session_id, player_id, bot.id, task_type, now()),
        )
        message = add_message(conn, session_id, "bot", opening) if opening else None
    model.flush()
    log.info("session started id=%s bot=%s task=%s", session_id, bot.id, task_type)
    return get_session(db, session_id), message


# --- Messages ------------------------------------------------------------------


def handle_message(state, session, bot, profile, content):
    """Store the player's message and produce the reply.

    Returns (player_message, reply, session_status). On ModelError the player's
    message stays stored and no reply is stored, so the browser can retry.
    The safety check always completes before any coaching reply is generated.
    """
    db = state.db
    history = get_messages(db, session["id"])
    last = history[-1] if history else None
    if last and last["sender"] == "player" and last["content"] == content:
        player_message = last  # a retry of a message that never got its reply
    else:
        with db.connect() as conn:
            player_message = add_message(conn, session["id"], "player", content)
        history.append(player_message)

    model = RecordedModel(state, session["id"])
    category = safety.check(model, bot.prompts, history[:-1], content)
    if category:
        reply = flag_session(state, session, bot, player_message, category)
        return player_message, reply, "flagged"

    text = conversation.coach_reply(
        model,
        bot,
        session["task_type"],
        profile,
        recent_summaries(db, session["player_id"], bot),
        history,
    )
    with db.connect() as conn:
        reply = add_message(conn, session["id"], "bot", text)
    return player_message, reply, "open"


def flag_session(state, session, bot, player_message, category):
    """Close the session for coaching and answer with the fixed safety message (FR-18, FR-19)."""
    with state.db.connect() as conn:
        conn.execute(
            "INSERT INTO safety_event (id, session_id, message_id, category, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_id(), session["id"], player_message["id"], category, now()),
        )
        reply = add_message(conn, session["id"], "system", bot.prompts.get("safety_message"))
        if bot.has_memory:
            # FR-23: the summary says only that the session ended early.
            save_summary(conn, session, bot.prompts.get("flagged_summary"))
        conn.execute(
            "UPDATE session SET status = 'flagged', ended_at = ?, summary_status = 'done'"
            " WHERE id = ?",
            (now(), session["id"]),
        )
    log.info("session flagged id=%s category=%s", session["id"], category)
    return reply


# --- End and summarize ---------------------------------------------------------


def end_session(state, session):
    """End an open session and try to summarize it. The player is never blocked (E-03)."""
    db = state.db
    changed = db.run(
        "UPDATE session SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'open'",
        (now(), session["id"]),
    )
    if not changed:
        raise SessionNotOpen()
    log.info("session ended id=%s", session["id"])
    summarize_session(state, session)
    return get_session(db, session["id"])


def summarize_session(state, session):
    """One attempt at the memory summary and the takeaway or outcome."""
    db = state.db
    bot = state.bots.get(session["bot_id"])
    if not bot or not bot.has_memory:
        # Nothing to summarize for a bot without memory (or one that was removed).
        db.run("UPDATE session SET summary_status = 'done' WHERE id = ?", (session["id"],))
        return
    try:
        text, result = summarizer.summarize(
            RecordedModel(state, session["id"]),
            bot.prompts,
            session["task_type"],
            get_messages(db, session["id"]),
        )
    except ModelError:
        db.run(
            "UPDATE session SET summary_status = 'failed', summary_attempts = summary_attempts + 1"
            " WHERE id = ?",
            (session["id"],),
        )
        log.warning("summary failed session=%s", session["id"])
        return
    result_column = "takeaway" if session["task_type"] == "post_loss" else "outcome"
    try:
        with db.connect() as conn:
            save_summary(conn, session, text)
            conn.execute(
                f"UPDATE session SET {result_column} = ?, summary_status = 'done',"
                " summary_attempts = summary_attempts + 1 WHERE id = ?",
                (result, session["id"]),
            )
    except sqlite3.IntegrityError:
        pass  # another request already summarized this session


def save_summary(conn, session, text):
    conn.execute(
        "INSERT INTO memory_summary (id, player_id, session_id, text, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), session["player_id"], session["id"], text, now()),
    )


def retry_failed_summaries(state, player_id):
    """Run on a player's next visit: one more try for each summary that failed."""
    for session in state.db.all(
        f"SELECT {SESSION_COLUMNS} FROM session WHERE player_id = ? AND status = 'ended'"
        " AND summary_status IN ('failed', 'pending') AND summary_attempts < ?",
        (player_id, MAX_SUMMARY_ATTEMPTS),
    ):
        summarize_session(state, session)
