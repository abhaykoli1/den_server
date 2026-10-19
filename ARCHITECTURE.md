# Rowdy's Den · Members App Architecture

This document describes the member-facing web application inside `den_server`.
The app is intentionally a **single FastAPI service**: Jinja2 renders the UI,
and the same service owns the JSON APIs and billing rules. There is no React
bundle, Node build, or second frontend server to deploy.

## Product boundary

`/members` is the member product. It is designed for a phone first, but uses
the same responsive layout on desktop:

- home dashboard with wallet, dues, live tables, cafe, pro shop and events
- live table status, rates and elapsed timers
- cafe and pro-shop catalog with search, categories, item detail and cart
- wallet, ledger, dues, UPI top-up requests and membership recharge
- wallet / tab / UPI checkout with stock reservation
- order receipts and fulfilment status polling
- table bookings, calendar, availability, confirmation ticket and `.ics`
- frames, rankings, tournaments, activity, profile, clubs and PWA install

## Request and trust boundaries

```text
Browser
  │
  ├─ GET /members/*
  │    Jinja2 SSR pages; reads `den_member` HttpOnly session cookie
  │
  ├─ POST /members/login/*
  │    email OTP or Google Identity Services credential → member session
  │
  └─ fetch /api/member-portal/*
       JSON mutations; sends Bearer JWT from window.DEN.token
       │
       ├─ app/routers/member_portal.py   member-only commands/read APIs
       ├─ app/portal.py                   shared member read model
       └─ existing billing services       ledger, stock, dues, plans, frames

Owner/staff console/API
  └─ /api/clubs/{club_id}/portal/*
       approve top-ups and membership requests; fulfil app orders
```

The server-rendered cookie is used to read a page and to establish the initial
API token. Money-changing browser actions do **not** use the cookie as API
authorisation; they call the member JSON API with a Bearer token. This keeps
mutations on one API surface and avoids adding form-based CSRF state-changing
routes.

`get_club()` rejects member-role tokens on admin routes. Member access is
explicitly allowed only where intended, such as booking availability and the
member portal. Owner/staff portal routes are protected separately.

## Code map

```text
app/
├── main.py                         FastAPI app, router registration, static mount
├── config.py                       environment and feature flags
├── db.py                           Mongo/MongoMock connection and collections
├── auth.py                         JWT, Google ID-token verification, user/member lookup
├── portal.py                       member read-model and view shaping
├── routers/
│   ├── members_web.py               Jinja pages, login/session cookie, image proxy
│   ├── member_portal.py             member JSON API (Bearer token)
│   ├── portal_admin.py              owner/staff portal JSON API
│   ├── auth.py                      existing app auth API
│   └── bookings.py                  shared booking rules
└── services.py                      club access, billing and ledger helpers

templates/members/
├── base.html                        shell, theme, PWA metadata, module boot data
├── _macros.html                     icons, cards, rows, empty states
├── login*.html                       Google/email sign-in and OTP verification
└── *.html                           SSR screens (home, cafe, shop, wallet, …)

static/members/
├── app.css                           single design system, warm cafe theme + dark
├── js/ui.js                          selectors, API, cart, sheets, tabs, theme
├── js/flows.js                       checkout, dues, UPI, plans, events, cancel
├── js/pages.js                       timers, cart page, booking form/calendar
├── demo/*.jpg                        demo catalogue and hero photography
└── icons/*                           PWA icons
```

The browser enhancement layer is split by responsibility. All three JS files
are plain browser scripts loaded with `defer` in dependency order:

1. `ui.js` defines `window.DENx`.
2. `flows.js` consumes those primitives for action flows.
3. `pages.js` consumes both for page-specific behaviour.

This keeps the SSR templates usable without a frontend build and makes it
possible to test individual behaviours with ordinary browser tooling.

## Rendering model

`members_web.py` creates a small Jinja environment with Indian-money, date/time,
initials, hue, duration and JSON-safe filters. `render()` adds common values:

- current theme and page scheme (`club`, `cafe`, `paper`)
- club/member summary and API token
- wallet, due, UPI and polling values
- cache-busting app version

Templates contain no database queries. They receive view dictionaries from
`portal.py` or router-specific page context. A page may poll the API for live
tables or fulfilment status, but it never talks directly to MongoDB.

## Authentication

### Email

1. Member enters an email already linked to a club member profile.
2. Production sends a short-lived, HMAC-hashed six-digit OTP.
3. Successful verification calls the same member-user flow as dev login.
4. A JWT is placed in the `den_member` HttpOnly, SameSite=Lax cookie.

`MEMBERS_WEB_LOGIN_OTP=false` can be used for local demos; the seeded demo
login then goes straight through. Invalid email, expired code and attempt-limit
paths all render the same complete login shell so the Google button remains
available.

### Google

When `MEMBERS_WEB_GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_ID` is set, the login page
renders the Google Identity Services pill button. Google posts a credential to
`POST /members/login/google`; the backend verifies signature, issuer, audience
and verified email before matching the email to a member profile.

In `AUTH_DEV_MODE`, if no client ID is configured, the page can show a clearly
labelled **demo Google** chooser. It is only a local convenience and the
endpoint returns 403 when dev mode is off. Production Google login always uses
real Google ID-token verification.

Owners and staff are redirected away from the members app; they use the main
billing application.

## Data and billing invariants

Member views are derived from the existing billing collections:

- `members.walletBalance`, `dueAmount`, passes and membership fields
- `sessions`, `frames`, `item_bills`, `payments` / payment logs
- `menu_items`, `tables`, `bookings`, `tournaments`, `plans`
- `wallet_topup_requests`, `membership_requests`, `login_otps`

App orders are `item_bills` with `source=member_order`, requested payment mode,
delivery destination and fulfilment state. The existing stock guards, wallet
debits, due creation and ledger/payment helpers remain the source of truth.
The member UI never invents a second wallet ledger.

UPI is intentionally a verification workflow rather than a fake success:
member opens a deep link or scans the club QR, submits a UTR, and the owner or
staff confirms it through `portal_admin.py`. Wallet and plan balances update
only after confirmation.

## One-click lifecycle

```bash
./start-members-app.sh             # demo, seed if needed, serve on :8000
./start-members-app.sh --fresh     # clear .devdata and reseed
./start-members-app.sh --prod      # use real .env Mongo/JWT/SMTP settings
start-members-app.bat              # Windows equivalent
```

Demo mode exports `MONGODB_URI=mongomock://demo` and `AUTH_DEV_MODE=true`.
The persistence adapter snapshots demo collections to `.devdata/snapshot.json`.
The script creates `.venv`, installs `requirements.txt`, serves on
`0.0.0.0`, and keeps the browser app and API on the same origin.

## Extension rules

When adding a member feature:

1. Add read shaping to `portal.py`, not to a template.
2. Add a member command/query to `member_portal.py` and enforce ownership.
3. Reuse existing billing/ledger helpers for money and stock.
4. Add owner approval/fulfilment to `portal_admin.py` when the action needs staff.
5. Add the page as a Jinja template and use `data-*` hooks for enhancement.
6. Keep the responsive warm design tokens in `app.css`; do not add page-local
   competing colour systems.
7. Update `MEMBERS_WEB.md` and add a smoke/E2E check for every mutation.
