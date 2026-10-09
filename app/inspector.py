"""Records every model call so it can be inspected: what was sent and what came back.

Shown in the inspector panel beside the chat and in the reviewer's session view.

Not part of SPEC.md: added at the product owner's request as a debugging aid.
"""
import json
import time

from .db import new_id, now
from .model_client import ModelError


class RecordedModel:
    """Stands in for the model client and stores each call against a session.

    With `defer=True` nothing is written until `flush()`, for calls made before
    the session row exists (the opening message).
    """

    def __init__(self, state, session_id, defer=False):
        self._model = state.model
        self._db = state.db
        self._session_id = session_id
        self._defer = defer
        self._pending = []

    def complete(self, purpose, system, messages):
        record = {
            "id": new_id(),
            "session_id": self._session_id,
            "purpose": purpose,
            "model": self._model.name,
            "system": system,
            "messages": json.dumps(messages),
            "response": None,
            "error": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "created_at": now(),
        }
        started = time.monotonic()
        try:
            reply, usage = self._model.complete_with_usage(purpose, system, messages)
        except ModelError as exc:
            record["error"] = str(exc)
            raise
        else:
            record["response"] = reply
            record["prompt_tokens"] = usage.get("prompt_tokens")
            record["completion_tokens"] = usage.get("completion_tokens")
            return reply
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            self._pending.append(record)
            if not self._defer:
                self.flush()

    def flush(self):
        self._defer = False
        records, self._pending = self._pending, []
        with self._db.connect() as conn:
            conn.executemany(
                "INSERT INTO model_call (id, session_id, purpose, model, system, messages,"
                " response, error, prompt_tokens, completion_tokens, duration_ms, created_at)"
                " VALUES (:id, :session_id, :purpose, :model, :system, :messages, :response,"
                " :error, :prompt_tokens, :completion_tokens, :duration_ms, :created_at)",
                records,
            )


def calls_for_session(db, session_id):
    calls = db.all(
        "SELECT id, session_id, purpose, model, system, messages, response, error,"
        " prompt_tokens, completion_tokens, duration_ms, created_at"
        " FROM model_call WHERE session_id = ? ORDER BY rowid",
        (session_id,),
    )
    for call in calls:
        call["messages"] = json.loads(call["messages"])
    return calls
