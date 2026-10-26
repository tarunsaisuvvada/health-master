import os
import time

os.environ["SESSION_SECRET"] = "x" * 32
os.environ["GOOGLE_CLIENT_ID"] = "test-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["DEMO"] = "0"

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

import app as m

client = TestClient(m.app, base_url="http://localhost:8000")


def ms(y, mo, d, h=0):
    return str(int(datetime(y, mo, d, h).timestamp() * 1000))


def test_merge_buckets_steps_and_hr():
    day = m.local_day(ms(2026, 10, 25, 12))
    rows = {day: {}}
    m.merge_buckets(rows, "steps", {"bucket": [{"startTimeMillis": ms(2026, 10, 25), "dataset": [
        {"point": [{"value": [{"intVal": 3000}]}, {"value": [{"intVal": 1200}]}]}]}]})
    m.merge_buckets(rows, "heart_rate", {"bucket": [{"startTimeMillis": ms(2026, 10, 25), "dataset": [
        {"point": [{"value": [{"fpVal": 72.4}, {"fpVal": 110}, {"fpVal": 58.6}]}]}]}]})
    assert rows[day] == {"steps": 4200, "avg_hr": 72, "min_hr": 59}


def test_merge_sleep_counts_on_wake_day():
    day = m.local_day(ms(2026, 10, 25))
    rows = {day: {}}
    m.merge_sleep(rows, {"session": [{"startTimeMillis": ms(2026, 10, 24, 23), "endTimeMillis": ms(2026, 10, 25, 6)}]})
    assert rows[day] == {"sleep_hours": 7.0}


@pytest.fixture
def los_angeles():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_bucket_after_dst_end_keeps_its_date(los_angeles):
    # clocks go back 2026-11-01; the 24h bucket for Nov 2 starts at 23:00 on Nov 1
    start = int(datetime(2026, 10, 31).timestamp() * 1000)
    rows = {"2026-10-31": {}, "2026-11-01": {}, "2026-11-02": {}}
    buckets = [{"startTimeMillis": str(start + i * m.DAY_MS), "dataset": [{"point": [{"value": [{"intVal": i + 1}]}]}]}
               for i in range(3)]
    m.merge_buckets(rows, "steps", {"bucket": buckets})
    assert [r["steps"] for r in rows.values()] == [1, 2, 3]


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


def test_expired_google_sign_in_returns_clear_error(tmp_path):
    (tmp_path / "mom@gmail.com.json").write_text("{}")
    with patch.object(m, "TOKENS", tmp_path), \
         patch.object(m.Credentials, "from_authorized_user_file"), \
         patch.object(m, "AuthorizedSession") as session:
        session.return_value.post.side_effect = RefreshError("invalid_grant")
        r = signed_in_client().get("/api/health?profile=mom@gmail.com")
    assert r.status_code == 409
    assert "sign in again" in r.json()["detail"]
