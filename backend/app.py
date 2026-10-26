import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # plain http is fine on localhost
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google reorders/adds scopes in the reply

BASE = Path(__file__).parent
TOKENS = BASE / "tokens"
TOKENS.mkdir(mode=0o700, exist_ok=True)
DATA = BASE / "data"  # fetched days per account, merged on every refresh
DATA.mkdir(mode=0o700, exist_ok=True)
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/callback")
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
]
FIT = "https://www.googleapis.com/fitness/v1/users/me"
DAY_MS = 86_400_000
METRICS = [
    ("steps", "com.google.step_count.delta"),
    ("active_minutes", "com.google.active_minutes"),
    ("heart_rate", "com.google.heart_rate.bpm"),
]

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
DEMO = os.getenv("DEMO") == "1"  # sample data, no Google sign-in
DEMO_EMAIL = "demo@example.com"

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if len(SESSION_SECRET) < 32 or SESSION_SECRET.startswith("change-me"):
    raise SystemExit("Set SESSION_SECRET in backend/.env to a random string of 32+ characters.")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


def make_flow(state=None):
    config = {"web": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}
    return Flow.from_client_config(config, scopes=SCOPES, state=state, redirect_uri=REDIRECT_URI,
                                   autogenerate_code_verifier=True)


def profiles():
    return sorted(p.stem for p in TOKENS.glob("*.json"))


def local_day(ms):
    return datetime.fromtimestamp(int(ms) / 1000).date().isoformat()


def rfc3339(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge_buckets(rows, key, resp):
    for bucket in resp.get("bucket", []):
        # label by bucket midpoint: after a DST shift buckets start at 23:00 or 01:00
        day = local_day(int(bucket["startTimeMillis"]) + DAY_MS // 2)
        points = [p for ds in bucket.get("dataset", []) for p in ds.get("point", [])]
        if not points or day not in rows:
            continue
        if key == "heart_rate":  # summary point values: [avg, max, min]
            rows[day]["avg_hr"] = round(sum(p["value"][0]["fpVal"] for p in points) / len(points))
            rows[day]["min_hr"] = round(min(p["value"][2]["fpVal"] for p in points))
        else:
            rows[day][key] = sum(p["value"][0].get("intVal", 0) for p in points)


def merge_sleep(rows, resp):
    for s in resp.get("session", []):
        day = local_day(s["endTimeMillis"])  # count sleep on the day they woke up
        if day in rows:
            hours = (int(s["endTimeMillis"]) - int(s["startTimeMillis"])) / 3_600_000
            rows[day]["sleep_hours"] = round(rows[day].get("sleep_hours", 0) + hours, 1)


def fetch_health(profile, days):
    if profile not in profiles():
        raise HTTPException(404, "Unknown profile")
    creds = Credentials.from_authorized_user_file(TOKENS / f"{profile}.json", SCOPES)
    api = AuthorizedSession(creds)
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = start_ms + days * DAY_MS
    rows = {(start + timedelta(days=i)).date().isoformat(): {} for i in range(days)}
    errors = []

    try:
        for key, dtype in METRICS:
            r = api.post(f"{FIT}/dataset:aggregate", json={
                "aggregateBy": [{"dataTypeName": dtype}],
                "bucketByTime": {"durationMillis": DAY_MS},
                "startTimeMillis": start_ms,
                "endTimeMillis": end_ms,
            })
            if r.ok:
                merge_buckets(rows, key, r.json())
            else:
                errors.append(f"{key}: {r.status_code} {r.text[:200]}")

        r = api.get(f"{FIT}/sessions", params={
            "activityType": 72, "startTime": rfc3339(start_ms), "endTime": rfc3339(end_ms)})
        if r.ok:
            merge_sleep(rows, r.json())
        else:
            errors.append(f"sleep: {r.status_code} {r.text[:200]}")
    except RefreshError:
        # Testing-mode OAuth apps get refresh tokens that expire after 7 days
        raise HTTPException(409, f"Google sign-in for {profile} has expired. Click + Add account and sign in again.")

    return {"profile": profile, "days": [{"date": d, **v} for d, v in rows.items()], "errors": errors}


def demo_health(profile, days):
    start = datetime.now().date() - timedelta(days=days - 1)
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "steps": 3000 + (i * 977) % 5000,
             "sleep_hours": round(6 + (i * 0.7) % 2.5, 1), "avg_hr": 70 + i % 6, "min_hr": 56 + i % 5,
             "active_minutes": 15 + (i * 13) % 40} for i in range(days)]
    return {"profile": profile, "days": rows, "errors": []}


def current_email(request: Request):
    email = request.session.get("email")
    if not email:
        raise HTTPException(401, "Not signed in")
    return email


@app.get("/auth/login")
def login(request: Request):
    if DEMO:
        request.session["email"] = DEMO_EMAIL
        return RedirectResponse(f"{FRONTEND_URL}/dashboard")
    flow = make_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent select_account")
    request.session["state"] = state
    request.session["verifier"] = flow.code_verifier
    return RedirectResponse(url)


@app.get("/auth/callback")
def callback(request: Request, error: str | None = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/?error={quote(error)}")
    state = request.session.pop("state", None)
    verifier = request.session.pop("verifier", None)
    if not state or request.query_params.get("state") != state:
        return RedirectResponse(f"{FRONTEND_URL}/?error=InvalidState")
    flow = make_flow(state)
    flow.code_verifier = verifier
    try:
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        email = AuthorizedSession(creds).get("https://openidconnect.googleapis.com/v1/userinfo").json()["email"]
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/?error={type(e).__name__}")
    token_file = TOKENS / f"{email}.json"
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    request.session["email"] = email
    return RedirectResponse(f"{FRONTEND_URL}/dashboard?profile={quote(email)}")


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    email = current_email(request)
    return {"email": email, "profiles": [DEMO_EMAIL] if DEMO else profiles()}


def cache_path(profile):
    if profile not in profiles():
        raise HTTPException(404, "Unknown profile")
    return DATA / f"{profile}.json"


def load_cache(profile):
    path = cache_path(profile)
    return json.loads(path.read_text()) if path.exists() else {"days": {}, "fetched_at": None, "errors": []}


def cached_view(profile, days, cache):
    start = datetime.now().date() - timedelta(days=days - 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    return {"profile": profile, "days": [{"date": d, **cache["days"].get(d, {})} for d in dates],
            "errors": cache["errors"], "fetched_at": cache["fetched_at"]}


def refresh_cache(profile, days):
    fresh = fetch_health(profile, days)
    cache = load_cache(profile)
    for day in fresh["days"]:
        date = day.pop("date")
        if day:  # never overwrite stored history with an empty fetch
            cache["days"][date] = day
    cache["fetched_at"] = datetime.now(timezone.utc).isoformat()
    cache["errors"] = fresh["errors"]
    tmp = cache_path(profile).with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
    tmp.chmod(0o600)
    tmp.replace(cache_path(profile))
    return cache


@app.get("/api/health")
def health(request: Request, profile: str | None = None, days: int = 7):
    email = current_email(request)
    days = max(1, min(days, 30))
    if DEMO:
        return demo_health(DEMO_EMAIL, days)
    profile = profile or email
    cache = load_cache(profile)
    if cache["fetched_at"] is None:  # first view of this account: fetch once
        cache = refresh_cache(profile, 30)
    return cached_view(profile, days, cache)


@app.post("/api/refresh")
def refresh(request: Request, profile: str | None = None, days: int = 7):
    email = current_email(request)
    days = max(1, min(days, 30))
    if DEMO:
        return {**demo_health(DEMO_EMAIL, days), "fetched_at": datetime.now(timezone.utc).isoformat()}
    profile = profile or email
    return cached_view(profile, days, refresh_cache(profile, days))
