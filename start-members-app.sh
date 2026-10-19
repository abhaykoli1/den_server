#!/usr/bin/env bash
# One-click start for the Rowdy's Den Members App (+ the full billing API).
#
#   ./start-members-app.sh            demo mode: in-memory Mongo + seeded club, no setup
#   ./start-members-app.sh --fresh    wipe the demo data and reseed
#   ./start-members-app.sh --prod     real MongoDB from .env (MONGODB_URI, JWT_SECRET, SMTP…)
#
# Opens http://localhost:8000/members  ·  demo logins: ravi@example.com · arjun@example.com
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

MODE="demo"; FRESH=0
for a in "$@"; do
  case "$a" in
    --fresh) FRESH=1 ;;
    --prod)  MODE="prod" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  esac
done

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "✗ python3 not found — install Python 3.10+ first"; exit 1; }

# --- virtualenv + dependencies (created once, reused after) -----------------
if [ ! -d ".venv" ]; then
  echo "▸ creating virtualenv (.venv) …"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c "import fastapi, motor, mongomock, jinja2, multipart, jwt" >/dev/null 2>&1; then
  echo "▸ installing dependencies …"
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
fi

PORT="${PORT:-8000}"

if [ "$MODE" = "demo" ]; then
  export MONGODB_URI="mongomock://demo" AUTH_DEV_MODE=true
  [ "$FRESH" = 1 ] && { echo "▸ clearing demo data …"; rm -rf .devdata; }
  if [ ! -f ".devdata/snapshot.json" ]; then
    echo "▸ seeding demo club (Rowdy's Den · 3 tables · cafe + pro shop · members) …"
    python scripts/seed_demo.py
  fi
else
  [ -f ".env" ] || { echo "✗ --prod needs a .env with MONGODB_URI, JWT_SECRET (see README)"; exit 1; }
fi

cat <<BANNER

  🎱  Rowdy's Den — Members App
  ─────────────────────────────────────────────
  Members app   http://localhost:${PORT}/members
  Billing API   http://localhost:${PORT}/api
BANNER
if [ "$MODE" = "demo" ]; then cat <<BANNER
  Demo logins   ravi@example.com   (wallet ₹500 · bookings · orders)
                arjun@example.com (due ₹170 · frame pass)
  Owner login   owner@rowdys.dev  (POST /api/auth/dev — approve top-ups & orders)
  Data lives in .devdata/snapshot.json — run with --fresh to reset.
BANNER
fi
echo "  Ctrl+C stops the server"
echo "  ─────────────────────────────────────────────"
echo
exec env PORT="$PORT" python run.py
