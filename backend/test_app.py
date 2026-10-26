import json
import os

os.environ["SESSION_SECRET"] = "x" * 32
os.environ["GOOGLE_CLIENT_ID"] = "test-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["DEMO"] = "0"

from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

import app as m


def civil(y, mo, d):
    return {"date": {"year": y, "month": mo, "day": d}}


def test_merge_rollup_steps_hr_active_minutes():
    rows = {"2026-10-25": {}}
    m.merge_rollup(rows, {"rollupDataPoints": [
        {"civilStartTime": civil(2026, 10, 25), "steps": {"countSum": "4200"}},
        {"civilStartTime": civil(2026, 10, 25),
         "heartRate": {"beatsPerMinuteAvg": 72.4, "beatsPerMinuteMax": 110, "beatsPerMinuteMin": 58.6}},
        {"civilStartTime": civil(2026, 10, 25), "activeMinutes": {"activeMinutesRollupByActivityLevel": [
            {"activityLevel": "LIGHT", "activeMinutesSum": "20"}, {"activityLevel": "VIGOROUS", "activeMinutesSum": "10"}]}},
        {"civilStartTime": civil(2026, 9, 1), "steps": {"countSum": "1"}},  # outside range: ignored
    ]})
    assert rows == {"2026-10-25": {"steps": 4200, "avg_hr": 72, "min_hr": 59, "active_minutes": 30}}


def test_merge_points_sleep_on_wake_day_and_resting_hr():
    rows = {"2026-10-25": {}}
    m.merge_points(rows, {"dataPoints": [
        {"sleep": {"interval": {"civilStartTime": civil(2026, 10, 24), "civilEndTime": civil(2026, 10, 25)},
                   "summary": {"minutesAsleep": "390"}}},
        {"sleep": {"interval": {"civilEndTime": civil(2026, 10, 25)}, "summary": {"minutesAsleep": "30"}}},  # nap
        {"dailyRestingHeartRate": {"date": {"year": 2026, "month": 10, "day": 25}, "beatsPerMinute": "61"}},
    ]})
    assert rows == {"2026-10-25": {"sleep_hours": 7.0, "resting_hr": 61}}


def test_api_requires_sign_in():
    assert TestClient(m.app).get("/api/health").status_code == 401
    assert TestClient(m.app).get("/api/me").status_code == 401


def test_callback_without_login_state_is_rejected():
    r = TestClient(m.app).get("/auth/callback?code=x&state=forged", follow_redirects=False)
    assert r.headers["location"].endswith("/?error=InvalidState")


def signed_in_client():
    c = TestClient(m.app)
    with patch.object(m, "DEMO", True):
        c.get("/auth/login", follow_redirects=False)
    return c


def test_unknown_profile_is_404():
    assert signed_in_client().get("/api/health?profile=../app").status_code == 404


def test_old_fit_only_sign_in_asks_to_sign_in_again(tmp_path):
    (tmp_path / "mom@gmail.com.json").write_text(json.dumps({"scopes": ["openid"]}))
    with patch.object(m, "TOKENS", tmp_path):
        r = signed_in_client().post("/api/refresh?profile=mom@gmail.com")
    assert r.status_code == 409
    assert "sign in again" in r.json()["detail"]


def test_cache_fetches_once_and_keeps_history(tmp_path):
    (tmp_path / "mom@gmail.com.json").write_text("{}")
    (tmp_path / "data").mkdir()
    today = datetime.now().date().isoformat()
    responses = [
        {"profile": "mom@gmail.com", "days": [{"date": today, "steps": 500}], "errors": []},
        {"profile": "mom@gmail.com", "days": [{"date": today}], "errors": []},  # Google returns nothing
    ]
    with patch.object(m, "TOKENS", tmp_path), patch.object(m, "DATA", tmp_path / "data"), \
         patch.object(m, "fetch_health", side_effect=responses) as fetch:
        c = signed_in_client()
        first = c.get("/api/health?profile=mom@gmail.com").json()
        second = c.get("/api/health?profile=mom@gmail.com").json()
        refreshed = c.post("/api/refresh?profile=mom@gmail.com").json()
    assert fetch.call_count == 2  # first view + explicit refresh, not the second view
    assert first["days"][-1]["steps"] == second["days"][-1]["steps"] == 500
    assert refreshed["days"][-1]["steps"] == 500  # empty fetch kept stored history
    assert refreshed["fetched_at"]


def test_expired_google_sign_in_returns_clear_error(tmp_path):
    (tmp_path / "mom@gmail.com.json").write_text(json.dumps({"scopes": m.SCOPES}))
    (tmp_path / "data").mkdir()
    with patch.object(m, "TOKENS", tmp_path), patch.object(m, "DATA", tmp_path / "data"), \
         patch.object(m.Credentials, "from_authorized_user_file"), \
         patch.object(m, "AuthorizedSession") as session:
        session.return_value.request.side_effect = RefreshError("invalid_grant")
        r = signed_in_client().get("/api/health?profile=mom@gmail.com")
    assert r.status_code == 409
    assert "expired" in r.json()["detail"]
