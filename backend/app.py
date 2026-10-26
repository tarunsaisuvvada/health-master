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
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]
HEALTH = "https://health.googleapis.com/v4/users/me/dataTypes"
ROLLUP_MAX_DAYS = 14  # dailyRollUp limit for heart-rate and active-minutes

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


def civil(d):
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def day_key(civil_dt):
    d = civil_dt["date"]
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def merge_rollup(rows, resp):
    for p in resp.get("rollupDataPoints", []):
        day = day_key(p["civilStartTime"])
        if day not in rows:
            continue
        if "steps" in p:
            rows[day]["steps"] = int(p["steps"].get("countSum", 0))
        if "heartRate" in p:
            rows[day]["avg_hr"] = round(p["heartRate"]["beatsPerMinuteAvg"])
            rows[day]["min_hr"] = round(p["heartRate"]["beatsPerMinuteMin"])
        if "activeMinutes" in p:
            levels = p["activeMinutes"].get("activeMinutesRollupByActivityLevel", [])
            rows[day]["active_minutes"] = sum(int(l.get("activeMinutesSum", 0)) for l in levels)


def merge_points(rows, resp):
    for p in resp.get("dataPoints", []):
        if "sleep" in p:  # count sleep on the day they woke up
            day = day_key(p["sleep"]["interval"]["civilEndTime"])
            minutes = int(p["sleep"].get("summary", {}).get("minutesAsleep", 0))
            if day in rows and minutes:
                rows[day]["sleep_hours"] = round(rows[day].get("sleep_hours", 0) + minutes / 60, 1)
        elif "dailyRestingHeartRate" in p:
            r = p["dailyRestingHeartRate"]
            day = day_key({"date": r["date"]})
            if day in rows:
                rows[day]["resting_hr"] = int(r["beatsPerMinute"])


def fetch_health(profile, days):
    if profile not in profiles():
        raise HTTPException(404, "Unknown profile")
    token_file = TOKENS / f"{profile}.json"
    if not set(SCOPES) <= set(json.loads(token_file.read_text()).get("scopes", [])):
        raise HTTPException(409, f"{profile} needs to sign in again to allow Google Health access. Click + Add account.")
    api = AuthorizedSession(Credentials.from_authorized_user_file(token_file, SCOPES))
    start = datetime.now().date() - timedelta(days=days - 1)
    rows = {(start + timedelta(days=i)).isoformat(): {} for i in range(days)}
    end = start + timedelta(days=days)
    errors = []

    def call(label, method, url, **kwargs):
        r = api.request(method, url, **kwargs)
        if not r.ok:
            try:
                message = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = r.text[:200]
            errors.append(f"{label}: {r.status_code} {message}")
            return {}
        return r.json()

    try:
        for dtype in ("steps", "heart-rate", "active-minutes"):
            chunk = start
            while chunk < end:
                chunk_end = min(chunk + timedelta(days=ROLLUP_MAX_DAYS), end)
                merge_rollup(rows, call(dtype, "POST", f"{HEALTH}/{dtype}/dataPoints:dailyRollUp", json={
                    "range": {"start": civil(chunk), "end": civil(chunk_end)}, "windowSizeDays": 1}))
                chunk = chunk_end

        sleep_filter = (f'sleep.interval.civil_end_time >= "{start.isoformat()}" '
                        f'AND sleep.interval.civil_end_time < "{end.isoformat()}"')
        page = {"filter": sleep_filter, "pageSize": 25}
        while True:
            resp = call("sleep", "GET", f"{HEALTH}/sleep/dataPoints", params=page)
            merge_points(rows, resp)
            if not resp.get("nextPageToken"):
                break
            page["pageToken"] = resp["nextPageToken"]

        # newest first; one point per day, so days + a margin covers the range
        merge_points(rows, call("resting heart rate", "GET", f"{HEALTH}/daily-resting-heart-rate/dataPoints",
                                params={"pageSize": days + 7}))
    except RefreshError:
        # Testing-mode OAuth apps get refresh tokens that expire after 7 days
        raise HTTPException(409, f"Google sign-in for {profile} has expired. Click + Add account and sign in again.")

    if errors and all("not linked to Google Health" in e for e in errors):
        errors = [f"{profile} has no Fitbit or Google Health data. Sign in with the Google account the Fitbit uses."]
    return {"profile": profile, "days": [{"date": d, **v} for d, v in rows.items()], "errors": errors}


def demo_health(profile, days):
    start = datetime.now().date() - timedelta(days=days - 1)
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "steps": 3000 + (i * 977) % 5000,
             "sleep_hours": round(6 + (i * 0.7) % 2.5, 1), "avg_hr": 70 + i % 6, "min_hr": 56 + i % 5, "resting_hr": 60 + i % 4,
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
