#!/usr/bin/env bash
# Sets up (first run only) and starts backend + frontend. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  sed -i.bak "s/^SESSION_SECRET=.*/SESSION_SECRET=$secret/" backend/.env && rm backend/.env.bak
  chmod 600 backend/.env
  echo "Created backend/.env. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (or set DEMO=1), then run ./start.sh again."
  exit 1
fi

if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
[ -d web/node_modules ] || npm --prefix web install --silent

trap 'kill 0' EXIT
(cd backend && .venv/bin/python -m uvicorn app:app --port 8000) &
npm --prefix web run dev &
echo "Open http://localhost:3000"
wait
