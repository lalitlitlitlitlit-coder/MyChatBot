"""M1: player identity, reviewer access, startup checks."""
import base64
import hashlib
import inspect
import re
import subprocess

import pytest

from app import identity
from app.config import ROOT, ConfigError, load_settings
from app.identity import PLAYER_COOKIE, REVIEWER_COOKIE
from tests.conftest import ACCESS_CODE

GOOD_ENV = {
    "REVIEWER_ACCESS_CODE": ACCESS_CODE,
    "SESSION_SECRET": "secret",
    "GROQ_API_KEY": "key",
}

# Every tester endpoint except POST /api/player (FR-02).
TESTER_ENDPOINTS = [
    ("GET", "/api/profile"),
    ("GET", "/api/bots"),
    ("POST", "/api/profile"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/some-id"),
    ("POST", "/api/sessions/some-id/messages"),
    ("POST", "/api/sessions/some-id/end"),
    ("GET", "/api/sessions/some-id/calls"),
    ("GET", "/api/memory"),
    ("DELETE", "/api/memory/some-id"),
    ("DELETE", "/api/memory"),
]
REVIEW_ENDPOINTS = [
    "/api/review/sessions",
    "/api/review/sessions/some-id",
    "/api/review/sessions/some-id/calls",
]


def set_cookie_header(response, name):
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(name + "="))


# FR-01
def test_first_visit_creates_player_with_httponly_cookie(anonymous, db):
    response = anonymous.post("/api/player")
    assert response.status_code == 201
    assert response.json()["has_profile"] is False
    header = set_cookie_header(response, PLAYER_COOKIE)
    assert "HttpOnly" in header
    token = anonymous.cookies[PLAYER_COOKIE]
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) * 8 >= 128
    assert db.one("SELECT COUNT(*) AS n FROM player")["n"] == 1


def test_repeat_visit_is_the_same_player(anonymous, db):
    first = anonymous.post("/api/player")
    second = anonymous.post("/api/player")
    assert second.status_code == 200
    assert second.json()["player_id"] == first.json()["player_id"]
    assert db.one("SELECT COUNT(*) AS n FROM player")["n"] == 1


def test_another_browser_is_a_new_player(new_client):
    a = new_client().post("/api/player").json()["player_id"]
    b = new_client().post("/api/player").json()["player_id"]
    assert a != b


def test_failed_creation_sets_no_cookie(anonymous, app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(app.state.db, "run", broken)
    response = anonymous.post("/api/player")
    assert response.status_code == 500
    assert response.json()["detail"] == "Something went wrong. Refresh the page."
    assert "set-cookie" not in response.headers


# FR-02
@pytest.mark.parametrize("method,path", TESTER_ENDPOINTS)
def test_tester_endpoints_need_a_valid_player_cookie(new_client, method, path):
    assert new_client().request(method, path).status_code == 401
    forged = new_client()
    forged.cookies.set(PLAYER_COOKIE, "made-up-token")
    assert forged.request(method, path).status_code == 401


# FR-04
def test_right_code_unlocks_review(anonymous):
    response = anonymous.post("/api/review/unlock", json={"access_code": ACCESS_CODE})
    assert response.status_code == 204
    assert "HttpOnly" in set_cookie_header(response, REVIEWER_COOKIE)
    assert anonymous.get("/api/review/sessions").status_code == 200


def test_wrong_code_is_rejected(anonymous):
    response = anonymous.post("/api/review/unlock", json={"access_code": "wrong-code-here"})
    assert response.status_code == 401
    assert REVIEWER_COOKIE not in anonymous.cookies


def test_sixth_wrong_code_within_a_minute_is_locked_out(anonymous, app):
    clock = [1000.0]
    app.state.limiter.clock = lambda: clock[0]
    for _ in range(5):
        assert anonymous.post("/api/review/unlock", json={"access_code": "nope"}).status_code == 401
    assert anonymous.post("/api/review/unlock", json={"access_code": "nope"}).status_code == 429
    # Even the right code is refused during the lockout.
    assert anonymous.post("/api/review/unlock", json={"access_code": ACCESS_CODE}).status_code == 429
    clock[0] += 61
    assert anonymous.post("/api/review/unlock", json={"access_code": ACCESS_CODE}).status_code == 204


def test_wrong_codes_spread_over_time_do_not_lock(anonymous, app):
    clock = [1000.0]
    app.state.limiter.clock = lambda: clock[0]
    for _ in range(8):
        assert anonymous.post("/api/review/unlock", json={"access_code": "nope"}).status_code == 401
        clock[0] += 20


@pytest.mark.parametrize("path", REVIEW_ENDPOINTS)
def test_review_endpoints_need_reviewer_cookie(anonymous, path):
    assert anonymous.get(path).status_code == 401
    anonymous.cookies.set(REVIEWER_COOKIE, "9999999999.forged")
    assert anonymous.get(path).status_code == 401


def test_expired_reviewer_cookie_is_rejected(anonymous, settings):
    anonymous.cookies.set(REVIEWER_COOKIE, identity.reviewer_cookie_value(settings.session_secret, clock=lambda: 0))
    assert anonymous.get("/api/review/sessions").status_code == 401


def test_lock_clears_reviewer_cookie(reviewer):
    assert reviewer.post("/api/review/lock").status_code == 204
    assert reviewer.get("/api/review/sessions").status_code == 401


# FR-05
@pytest.mark.parametrize("path", REVIEW_ENDPOINTS)
def test_player_cookie_does_not_open_review(player, path):
    assert player.get(path).status_code == 401


@pytest.mark.parametrize("method,path", TESTER_ENDPOINTS)
def test_reviewer_cookie_does_not_open_tester_endpoints(reviewer, method, path):
    assert reviewer.request(method, path).status_code == 401


# NFR-02
def test_only_token_hash_is_stored(player, db):
    token = player.cookies[PLAYER_COOKIE]
    row = db.one("SELECT * FROM player")
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.values()


def test_access_code_comparison_is_constant_time():
    assert "compare_digest" in inspect.getsource(identity.access_code_matches)


# NFR-03 / E-19
@pytest.mark.parametrize(
    "change,expected",
    [
        ({"REVIEWER_ACCESS_CODE": None}, "REVIEWER_ACCESS_CODE"),
        ({"REVIEWER_ACCESS_CODE": "short"}, "REVIEWER_ACCESS_CODE"),
        ({"SESSION_SECRET": None}, "SESSION_SECRET"),
        ({"GROQ_API_KEY": None}, "GROQ_API_KEY"),
    ],
)
def test_app_refuses_to_start_on_bad_config(change, expected):
    env = {k: v for k, v in {**GOOD_ENV, **change}.items() if v is not None}
    with pytest.raises(ConfigError, match=expected):
        load_settings(env)


def test_good_config_loads():
    assert load_settings(GOOD_ENV).model_provider == "groq"
    assert load_settings({**GOOD_ENV, "GROQ_API_KEY": "", "MODEL_PROVIDER": "fake"}).model_provider == "fake"


def test_no_secrets_in_repo_or_frontend():
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert ".env" not in tracked
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".css", ".txt", ".md", ".example"}:
            continue
        assert not re.search(r"gsk_[A-Za-z0-9]{20,}", path.read_text()), f"Groq key in {name}"
    for path in (ROOT / "static").glob("*.*s"):
        text = path.read_text()
        assert "GROQ_API_KEY" not in text and "REVIEWER_ACCESS_CODE" not in text


# NFR-01: the notice is the first screen and carries the required lines.
def test_notice_text_is_served(anonymous):
    html = anonymous.get("/").text
    for line in [
        "prototype for adult testers",
        "made-up scenarios",
        "Do not enter real personal information",
        "saved in this browser only",
    ]:
        assert line in html
    script = anonymous.get("/static/app.js").text
    assert "sessionStorage" in script and "noticeAcknowledged" in script


# NFR-11
def test_cookies_are_httponly_and_samesite_lax(new_client):
    player = new_client().post("/api/player")
    reviewer = new_client().post("/api/review/unlock", json={"access_code": ACCESS_CODE})
    for header in [set_cookie_header(player, PLAYER_COOKIE), set_cookie_header(reviewer, REVIEWER_COOKIE)]:
        assert "HttpOnly" in header
        assert "samesite=lax" in header.lower()
        assert "secure" not in header.lower()


def test_cookies_are_secure_over_https(app):
    from fastapi.testclient import TestClient

    response = TestClient(app, base_url="https://testserver").post("/api/player")
    assert "secure" in set_cookie_header(response, PLAYER_COOKIE).lower()
