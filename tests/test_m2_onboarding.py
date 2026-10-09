"""M2: onboarding profile."""
import pytest

from tests.conftest import PROFILE


# FR-07
def test_profile_is_saved_and_returned(player):
    response = player.post("/api/profile", json=PROFILE)
    assert response.status_code == 201
    saved = response.json()
    assert {k: saved[k] for k in PROFILE} == PROFILE
    assert player.get("/api/profile").json() == saved
    assert player.post("/api/player").json()["has_profile"] is True


def test_pressure_points_are_optional(player):
    body = {k: v for k, v in PROFILE.items() if k != "pressure_points"}
    response = player.post("/api/profile", json=body)
    assert response.status_code == 201
    assert response.json()["pressure_points"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"display_name": None},
        {"display_name": "   "},
        {"display_name": "x" * 51},
        {"age": None},
        {"age": 0},
        {"age": 100},
        {"age": "abc"},
        {"age": 16.5},
        {"playing_level": None},
        {"playing_level": "legend"},
        {"pressure_points": "x" * 501},
    ],
)
def test_invalid_profiles_are_rejected(player, change):
    body = {k: v for k, v in {**PROFILE, **change}.items() if v is not None}
    assert player.post("/api/profile", json=body).status_code == 422
    assert player.get("/api/profile").status_code == 404


# FR-08
def test_second_profile_is_refused(player):
    assert player.post("/api/profile", json=PROFILE).status_code == 201
    response = player.post("/api/profile", json={**PROFILE, "display_name": "Other"})
    assert response.status_code == 409
    assert player.get("/api/profile").json()["display_name"] == "Maya"


def test_no_profile_yet_is_404(player):
    assert player.get("/api/profile").status_code == 404


def test_frontend_skips_onboarding_when_profile_exists(player):
    script = player.get("/static/app.js").text
    assert "bot.needs_profile && !profile" in script
    html = player.get("/").text
    assert 'data-screen="onboarding"' in html and 'data-screen="home"' in html
