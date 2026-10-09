"""SQLite storage. Timestamps are UTC ISO 8601 strings; IDs are UUID strings."""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_profile (
    player_id TEXT PRIMARY KEY REFERENCES player(id),
    display_name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 1 AND 99),
    playing_level TEXT NOT NULL,
    pressure_points TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES player(id),
    -- Not in the spec's data model: which chatbot (a folder under bots/) ran the session.
    bot_id TEXT NOT NULL DEFAULT 'tennis',
    task_type TEXT NOT NULL,  -- one of that bot's task ids; checked in code
    status TEXT NOT NULL CHECK (status IN ('open', 'ended', 'flagged')),
    takeaway TEXT,
    outcome TEXT,
    summary_status TEXT NOT NULL CHECK (summary_status IN ('pending', 'done', 'failed')),
    -- Not in the spec's data model: counts tries so E-03 can retry exactly once.
    summary_attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(id),
    sender TEXT NOT NULL CHECK (sender IN ('player', 'bot', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seq INTEGER NOT NULL  -- insertion order within the session
);

CREATE TABLE IF NOT EXISTS memory_summary (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES player(id),
    session_id TEXT NOT NULL UNIQUE REFERENCES session(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS safety_event (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(id),
    message_id TEXT NOT NULL UNIQUE REFERENCES message(id),
    category TEXT NOT NULL CHECK (category IN ('self_harm', 'suicide', 'harm_to_others', 'abuse')),
    created_at TEXT NOT NULL
);

-- Not in the spec's data model: one row per model call, for the inspector.
CREATE TABLE IF NOT EXISTS model_call (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(id),
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    system TEXT NOT NULL,
    messages TEXT NOT NULL,  -- JSON list of {role, content}
    response TEXT,
    error TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS model_call_session ON model_call(session_id);
CREATE INDEX IF NOT EXISTS session_player ON session(player_id, started_at);
CREATE INDEX IF NOT EXISTS message_session ON message(session_id, seq);
CREATE INDEX IF NOT EXISTS memory_player ON memory_summary(player_id, created_at);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id():
    return str(uuid.uuid4())


class Database:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self._add_bot_id_to_sessions()

    def _add_bot_id_to_sessions(self):
        """Upgrade a database from before there were several chatbots.

        Old session tables have no bot_id and only allow the three tennis task
        types, so the table is rebuilt; every existing session is a tennis one.
        """
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(session)")]
            if "bot_id" in columns:
                return
            new_table = SCHEMA.split("CREATE TABLE IF NOT EXISTS session (")[1].split(");")[0]
            conn.executescript(
                "PRAGMA foreign_keys = OFF; BEGIN;"
                f"CREATE TABLE session_new ({new_table});"
                "INSERT INTO session_new (id, player_id, bot_id, task_type, status, takeaway,"
                " outcome, summary_status, summary_attempts, started_at, ended_at)"
                " SELECT id, player_id, 'tennis', task_type, status, takeaway, outcome,"
                " summary_status, summary_attempts, started_at, ended_at FROM session;"
                "DROP TABLE session; ALTER TABLE session_new RENAME TO session;"
                "CREATE INDEX IF NOT EXISTS session_player ON session(player_id, started_at);"
                "COMMIT;"
            )
        finally:
            conn.close()

    @contextmanager
    def connect(self):
        """One connection per unit of work; commits on success, rolls back on error."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def all(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def run(self, sql, params=()):
        """Execute one statement and return the number of rows it changed."""
        with self.connect() as conn:
            return conn.execute(sql, params).rowcount
