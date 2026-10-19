# Rowdy's Den — Club Billing · Backend (FastAPI)

The **authoritative billing engine**. Frontends (React site / Flutter app) only show
estimates — every rupee is computed and persisted here.

## Quick start (dev)

```bash
pip install -r requirements.txt
MONGODB_URI="mongomock://demo" AUTH_DEV_MODE=true python3 run.py
# http://0.0.0.0:8000 · docs at /docs · members app at /members
```

**Or use the quick start script:**
```bash
./start-members-app.sh          # macOS/Linux
start-members-app.bat           # Windows
```

## 📱 Members App (`/members`)

The member-facing app ships inside this backend — Jinja2-rendered, mobile-first,
installable as a PWA. One command starts everything:

```bash
./start-members-app.sh            # macOS / Linux   (creates .venv, installs, seeds, runs)
start-members-app.bat             # Windows
./start-members-app.sh --fresh    # reset demo data · --prod uses MongoDB from .env
```

Open **http://localhost:8000/members** · demo logins `ravi@example.com`
(wallet ₹500, bookings, orders) · `arjun@example.com` (₹170 due, frame pass).

What members get: a premium warm cafe-style UI with live tables, timers & rates ·
cafe + pro-shop (cues, tips, chalk, gloves, tees…) with cart → wallet / tab /
UPI checkout · orders with kitchen status · wallet ledger, dues & top-ups ·
frames history, stats, club rankings · table bookings (list, calendar, slot
picker, ticket, .ics) · membership plans · tournaments · activity feed · dark
mode · PWA install · real Google sign-in when configured.

Owner side: `GET/POST /api/clubs/{id}/portal/*` to approve UPI top-ups /
plan purchases and run the app-order kitchen queue. Full screens, API list and
data model are in **[MEMBERS_WEB.md](./MEMBERS_WEB.md)**; trust boundaries and
extension rules are in **[ARCHITECTURE.md](./ARCHITECTURE.md)**. Google setup is
in **[GOOGLE_OAUTH_SETUP.md](./GOOGLE_OAUTH_SETUP.md)**.

## Full Setup

Dev out of the box: `MONGODB_URI=mongomock://demo` runs fully in-memory with
**snapshot persistence** — every 5s + on shutdown all collections dump to
`.devdata/snapshot.json` and re-hydrate on boot. Logins and data survive restarts.
Delete that file for a fresh start.

```bash
python3 scripts/seed_demo.py  # ONCE only — demo club, players, tournaments
python3 -m pytest tests -q    # 34 tests · 247 checks (billing rules, walls, reports)
```

Demo logins (dev auth): `owner@rowdys.dev` (Raju Bhai) · `master@rowdys.dev`
(Master Boss) · `staff2@rowdys.dev` (Abhay Koli — staff via Master panel).

## Your `.env` (already in place)

- Real Atlas cluster (`MONGODB_URI=mongodb+srv://…rowdysden.xfypy3m…`) + `MONGODB_DB=rowdys_den`
  — keep the DB name identical to the Vercel env or a different DB opens.
- Gmail SMTP is wired (`smtp.gmail.com:587` + app password). App password been shared
  in chat — **rotate it**: Google Account → Security → App Passwords.
- `AUTH_DEV_MODE=false` locally (prod-like: Google sign-in only). For dev-login testing
  run with `AUTH_DEV_MODE=true MONGODB_URI=mongomock://demo python3 run.py`.

## The rules the tests lock

| Rule | Where |
| --- | --- |
| `tableAmount = max(minCharge, round(rate/60 × minutes, 2))` — server clock, never whole-rupee ceil | `app/billing.py` |
| Winner never pays · solo loser all · 2v2 losers split evenly | `resolve_sides` + `compute_bill` |
| Pass → wallet → cash → due; one frame/bill; due-holders pass-blocked | `compute_bill` |
| Old due first on member payments | `routers/members.py` |
| Monthly member % off TABLE money; winner bonus pockets winners | `compute_bill` |
| Gloves join AFTER discounts; returned gloves free | `compute_bill` |
| `billingLock` atomic — a session can never bill twice; validation runs BEFORE lock | `routers/sessions.py` |
| Winner correction = full reversal (wallets, dues, passes, bonus, ledger) → re-bill → ADMIN log | `routers/frames.py` |
| 402 subscription lock on billing mutations; staff 403 on money-admin surfaces (fires BEFORE 402) | `services.py` |
| Tenant isolation on every `/api/clubs/{id}/*` | `services.get_club` |
| Income = PAYMENT ledger, cash basis (IST days); `mode: wallet` consumption never double-counts | `routers/reports.py` |
| Restock → weighted cost + auto stock expense · tournament prizes → auto expenses | `routers/items.py`, `routers/tournaments.py` |

## Route map (~87 routes)

`health` · `auth` (google/dev/me PATCH) · `subscription-plans` · `account/subscription[…/select]`
· `clubs` (+settings/data/stats) · `tables` (+toggle-active) · `members` (+payments/notify)
· `plans` (+toggle/sell) · `sessions` (stop/resume/confirm/items/advance/move/gloves/return/delete)
· `frames` (+winners correction) · `logs` · `menu-items` (+restock) · `item-bills` (+mark-paid)
· `expenses`🔒 · `reports/monthly|finance|day-close|utilisation`🔒 · `tournaments`
(participants/start/matches/play/result/cancel · knockout + league) · `team`🔒 ·
`master/*` (overview/users/subscriptions/plans/mailouts) · `platform/support`.

Errors: 400 readable · 401 auth · 402 subscription · 403 role/tenant/admin-area ·
404 · 500 clean JSON (no stack traces) · 422 → 400 readable.

## Connect the React web frontend (localhost:5173)

Backend is browser-ready for the Vite dev server out of the box:

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 run.py            # serves http://localhost:8000/api  (Mongo ke bina bhi — mongomock dev)
curl http://localhost:8000/api/health    # {"ok":true,"db":"mongomock","authDevMode":true}
```

- **CORS** already allows `http://localhost:5173` / `http://127.0.0.1:5173` by default
  (prod overrides via `CORS_ORIGINS` env, e.g. Vercel domains).
- Web app ka API base: ya to `http://localhost:8000/api` direct, ya `VITE_API_URL=/api`
  + Vite proxy (error log se tumhara proxy already `127.0.0.1:8000` pe hai — bas backend ON rakho):
  ```js
  // vite.config.js (frontend repo)
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } }
  ```

### Login contract (web)

| Mode | Endpoint | Body |
|------|----------|------|
| Google | `POST /api/auth/google` | `{ "idToken": credentialResponse.credential }` — ya `{ "credential": ... }` (dono chalte hain) |
| Dev (local) | `POST /api/auth/dev` | `{ "email": "you@example.com", "name": "Abhay" }` |

Response dono ka: `{ user, token }` → `Authorization: Bearer <token>` har request me.

- ⚠️ **"idToken: Field required"** ka matlab: button ne credential nikala hi nahi.
  `@react-oauth/google` ka **`<GoogleLogin>` component** use karo — `onSuccess` me
  `credentialResponse.credential` hi idToken hai. `useGoogleLogin()` (custom button)
  sirf **access_token** deta hai — wo backend pe NAHI chalega.
- Google Cloud Console → OAuth client (web) → **Authorized JavaScript origins** me
  `http://localhost:5173` add karo, warna credential milega hi nahi.
- Backend `.env` me `GOOGLE_CLIENT_ID` = wahi web client (`375395125425-st5ba3…`).
  Local hacking ke liye `AUTH_DEV_MODE=true` (default in dev) → dev sign-in instant chalta hai.
- Real data chahiye to backend `.env` me `MONGODB_URI`/`MONGODB_DB` (Atlas) set karo —
  blank chhodo to in-memory mongomock + JSON snapshot se bina setup ke chal jata hai.
- Flutter app ko bhi yahi backend milega: `--dart-define=API_URL=http://10.0.2.2:8000/api` (emulator).



<!-- listen now i have den_server the backend and den_app in fluuter this is the billing app of rowdys den now i want to build a members app of rowdys den       
  where memers wants to see wheather the table/game is free or live  he also see his profile , wallet balance, membership, dues, advance  and he can pay from 
  app too and he can order food items from the app that is more secure and he has a activity transactions log and frames log that hi play at what time and    
  how mujhe did he pay and all make this app in flutter use backend already in my folder make another folder named member_app and start building the app   -->