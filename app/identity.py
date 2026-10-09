"""Who is calling: players by browser cookie, the reviewer by shared access code."""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from .db import new_id, now

log = logging.getLogger(__name__)

PLAYER_COOKIE = "player_token"
PLAYER_COOKIE_SECONDS = 365 * 24 * 60 * 60
REVIEWER_COOKIE = "reviewer_session"
REVIEWER_COOKIE_SECONDS = 12 * 60 * 60
MAX_WRONG_CODES = 5
LOCKOUT_SECONDS = 60


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def set_cookie(request, response, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


# --- Players -----------------------------------------------------------------


def find_player(request):
    """Return the Player row for this browser's cookie, or None."""
    token = request.cookies.get(PLAYER_COOKIE)
    if not token:
        return None
    db = request.app.state.db
    player = db.one("SELECT * FROM player WHERE token_hash = ?", (hash_token(token),))
    if player:
        db.run("UPDATE player SET last_seen_at = ? WHERE id = ?", (now(), player["id"]))
    return player


def create_player(request, response):
    token = secrets.token_urlsafe(32)  # 256 bits
    player = {"id": new_id(), "token_hash": hash_token(token), "created_at": now()}
    request.app.state.db.run(
        "INSERT INTO player (id, token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (player["id"], player["token_hash"], player["created_at"], player["created_at"]),
    )
    set_cookie(request, response, PLAYER_COOKIE, token, PLAYER_COOKIE_SECONDS)
    log.info("player created id=%s", player["id"])
    return player


def require_player(request: Request):
    player = find_player(request)
    if not player:
        raise HTTPException(401, "No player cookie. Reload the page.")
    return player


# --- Reviewer ----------------------------------------------------------------


def _sign(secret, expires):
    return hmac.new(secret.encode(), f"reviewer:{expires}".encode(), hashlib.sha256).hexdigest()


def reviewer_cookie_value(secret, clock=time.time):
    expires = int(clock()) + REVIEWER_COOKIE_SECONDS
    return f"{expires}.{_sign(secret, expires)}"


def is_reviewer(request, clock=time.time):
    expires, _, signature = request.cookies.get(REVIEWER_COOKIE, "").partition(".")
    if not expires.isdigit() or int(expires) <= clock():
        return False
    expected = _sign(request.app.state.settings.session_secret, int(expires))
    return hmac.compare_digest(signature.encode(), expected.encode())


def require_reviewer(request: Request):
    if not is_reviewer(request):
        raise HTTPException(401, "Reviewer access code required.")


def access_code_matches(submitted, expected):
    # Hash both sides so the constant-time comparison sees equal lengths.
    return hmac.compare_digest(
        hashlib.sha256(submitted.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


class AttemptLimiter:
    """Locks an IP out for a minute after 5 wrong access codes in a minute (FR-04)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._wrong = defaultdict(list)
        self._locked_until = {}

    def is_locked(self, ip):
        with self._lock:
            return self._locked_until.get(ip, 0) > self.clock()

    def record_wrong(self, ip):
        with self._lock:
            current = self.clock()
            recent = [t for t in self._wrong[ip] if current - t < LOCKOUT_SECONDS] + [current]
            if len(recent) >= MAX_WRONG_CODES:
                self._locked_until[ip] = current + LOCKOUT_SECONDS
                recent = []
            self._wrong[ip] = recent
