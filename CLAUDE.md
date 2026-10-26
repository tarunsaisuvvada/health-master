# Parent Health: instructions for Claude

Local dashboard for a parent's Google Fit data. FastAPI backend in `backend/` (port 8000), Next.js frontend in `web/` (port 3000). See README.md for the full picture.

## When the user says "start" (or "run", "open the app")

1. If `backend/.env` is missing, run `./start.sh` once: it creates `.env` with a random `SESSION_SECRET` and stops.
   Ask the user for the Google OAuth client ID and secret, or for the path of the `client_secret_*.json` they downloaded from Google Cloud Console.
   From the JSON, copy `web.client_id` and `web.client_secret` into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. Never print the secret.
   If they have no credentials yet, set `DEMO=1` to show sample data.
2. Run `./start.sh` in the background (or in a terminal tab the user can see). It installs dependencies on first run, then starts both servers.
3. Wait until http://localhost:3000 responds, then give the user that URL and tell them to click **Sign in with Google**.

Stop: Ctrl-C in the start.sh terminal stops both servers.

## Rules

- Never commit or print `backend/.env`, `backend/tokens/` or `backend/data/`: they hold the OAuth client secret, the parents' Google tokens and their health data.
- Health data is cached in `backend/data/<email>.json`. New data comes from Google only when the user clicks **Refresh** (or on an account's first view).
- This app must run on the user's own computer: Google only allows the `http://localhost:8000/auth/callback` redirect. Running it on a cloud server needs a new redirect URI and HTTPS.
- Only Gmail addresses added as **Test users** in the Google Cloud Console (Google Auth Platform → Audience) can sign in. A "403 access_denied" from Google means the account is not a test user.
- Sign-ins expire every 7 days while the OAuth app is in Testing mode; the dashboard then asks to sign in again.
- Tests: `cd backend && .venv/bin/python -m pytest -q`. Frontend check: `npm --prefix web run build`.
