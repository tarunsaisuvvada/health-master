# Parent Health

Local dashboard for a parent's Fitbit / Google Health data: steps, sleep, resting and average heart rate, active minutes. Data comes from the [Google Health API](https://developers.google.com/health), the successor to the Fitbit Web API.

- `backend/`: FastAPI on port 8000. Handles Google sign-in, stores tokens in `backend/tokens/`, serves `/api/me`, `/api/health` and `/api/refresh`.
- Fetched data is saved per account in `backend/data/<email>.json` and builds up over time. The dashboard reads from that file; Google is only called on an account's first view or when you click **Refresh**.
- `web/`: Next.js on port 3000. Login page and dashboard. Proxies `/api/*` and `/auth/*` to the backend.

## Admin (one time, Google Cloud Console)

1. **APIs & Services → Library:** enable the **Google Health API** (`health.googleapis.com`).
2. **Credentials:** the OAuth client must be type **Web application**, with this authorized redirect URI:
   `http://localhost:8000/auth/callback`
3. **OAuth consent screen → Test users:** add each Gmail address that will sign in.
4. Send the client ID and client secret (or the downloaded `client_secret_*.json`) to the user privately, never through the repo.

## Run

Needs Python 3.10+ and Node 20+.

```bash
./start.sh
```

The first run creates `backend/.env` with a random `SESSION_SECRET` and stops. Put the admin's `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in it (or set `DEMO=1`), then run `./start.sh` again. It installs dependencies the first time, starts both servers, and Ctrl-C stops both.

Using Claude Code? Open this folder and say **start**. [CLAUDE.md](CLAUDE.md) tells Claude how to set up `.env` and run the app.

Open **http://localhost:3000** (use `localhost`, not `127.0.0.1`) and click **Sign in with Google**.
Use **+ Add account** to connect a second parent; switch accounts from the dropdown.

Set `DEMO=1` in `backend/.env` to see the dashboard with sample data, without Google.

Never commit `.env` or `tokens/`: they hold the client secret and the parents' access tokens.

## Known limits

- **Google shows an "unverified app" warning** at sign-in, because the Google Health permissions are restricted and the app is not verified. Test users click **Advanced → Go to Health Master** to continue.
- Accounts signed in before the switch from Google Fit must sign in again with **+ Add account** to grant the Google Health permissions; the dashboard says so.
- **Sign-in expires every 7 days** while the OAuth app is in Testing mode. The dashboard then says so; click **+ Add account** and sign in with that parent's account again.
- Days are counted in this computer's timezone, not the parent's.

## Test

```bash
cd backend && pip install pytest && pytest
```
