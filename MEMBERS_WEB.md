# Rowdy's Den · Members App

A mobile-first web app for club members, served by this backend at **`/members`**.
Server-rendered Jinja2 pages + a small vanilla-JS layer; no build step, no
separate frontend. Installable as a PWA (manifest + icons) and works as a plain
website on any phone or desktop.

```
./start-members-app.sh            # macOS / Linux  → http://localhost:8000/members
start-members-app.bat             # Windows
./start-members-app.sh --fresh    # reset the demo data
./start-members-app.sh --prod     # real MongoDB from .env
```

Demo logins (no password): `ravi@example.com` · `arjun@example.com`

---

## What members can do

| Area | Screen | Highlights |
|---|---|---|
| **Home** | `/members` | wallet + due strip, category tiles, live-table strip with running timers, hero, Popular / Cafe / Store grid, events, recent frames & orders |
| **Live tables** | `/members/tables` | free / in-play / billing per table, elapsed timers, rates (hourly, minimum, glove, by-players, peak), "your game" highlight, auto-refresh |
| **Cafe** | `/members/cafe` | category chips, search, popular (90-day sales), order to *your* table when you're playing |
| **Pro shop** | `/members/shop` | cues, tips & ferrules, chalk, gloves, apparel, accessories · new arrivals / popular tabs · stock badges |
| **Product** | `/members/item/{id}` | photo, price, description, qty stepper, cafe preferences note, related items, share |
| **Cart & checkout** | `/members/cart` | deliver to table / counter, pay from **wallet**, **add to tab** (respects the club's due limit) or **UPI** (UTR, counter verifies) · stock reserved atomically |
| **Orders** | `/members/orders`, `/orders/{id}` | app + counter bills, month groups, receipt ticket, fulfilment tracker (placed → preparing → ready → served), pay an unpaid bill from wallet |
| **Wallet** | `/members/wallet` | balance, derived transaction ledger (table time, winner bonuses, orders, recharges, pass use, dues), dues breakdown, pay dues from wallet, UPI top-up requests, recharge offers |
| **My frames** | `/members/frames` | W/L, win-rate, streak, hours, spend · month groups · per-frame breakdown (who, table time, cafe items, how you paid, bonus) |
| **Rankings** | `/members/rankings` | 90-day club leaderboard (wins, then win-rate) with podium |
| **Bookings** | `/members/bookings` | upcoming/past list, month calendar with dots, **book** (table → day → duration → slot grid with taken/yours/past states), confirmation ticket, `.ics` calendar file, cancel |
| **Membership** | `/members/membership` | current plan progress, all plans (wallet / frame pass / monthly), buy from wallet or UPI request |
| **Events** | `/members/tournaments` | upcoming, live brackets progress, register (wallet or counter), withdraw, results |
| **Activity** | `/members/activity` | member-scoped log feed, unread badge |
| **More / Profile** | `/members/more`, `/profile` | stats, club contact/directions, dark mode, install, multi-club switch, edit name/phone, player card |

Sign-in: email on the player card → (optional) 6-digit code by email → session
cookie. When `MEMBERS_WEB_GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_ID` is configured,
the login page renders the real Google Identity Services pill button. In local
dev mode without a client ID, a clearly labelled demo Google chooser is available
for previewing the flow; it is disabled outside dev mode. Owners / staff are
refused (they use the billing app).

---

## Architecture

```
app/routers/members_web.py    pages   GET /members/*          cookie session (den_member = JWT)
app/routers/member_portal.py  JSON    /api/member-portal/*    Bearer token, member role only
app/routers/portal_admin.py   JSON    /api/clubs/{id}/portal/* owner/staff: approve requests, kitchen queue
app/portal.py                 shared read-model (catalog, tables, frames, bills, ledger, dues, bookings, …)
templates/members/*.html      Jinja2 pages (base + macros + 20 screens)
static/members/app.css        warm premium cafe design system + dark mode
static/members/js/ui.js       shared API/cart/sheet/tab/theme primitives
static/members/js/flows.js    checkout, dues, UPI, plans, events, cancellation
static/members/js/pages.js    timers, cart checkout, booking form/calendar, login demo
static/members/demo/*.jpg     demo product photos · static/members/icons/*  PWA icons
```

* Pages render from the cookie; **every mutation goes through the JSON API
  with the Bearer token** (`window.DEN.token`), so the cookie never authorises
  money movement (no CSRF surface).
* `get_club()` now rejects member tokens on all `/api/clubs/{id}/…` admin
  routes (bookings opt in with `allow_member=True`). Members only see their
  own data through `/api/member-portal/*`.
* All money paths reuse the existing ledger: wallet debits/credits update
  `members.walletBalance`, sales are written with `payment_log(...)`, due
  payments settle unpaid bills oldest-first, stock is decremented with
  `find_one_and_update` guards and restored on failure — so day-close and
  reports stay correct.
* Item photos: `menu_items.image` may be a URL/static path or a `data:` URL;
  data URLs are served as real images via `/members/img/{item_id}` (cached).

For the full request flow, trust boundaries, auth model and extension rules, see
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Member API (`/api/member-portal`)

```
GET  home · dashboard · me · tables · catalog · items/{id}
GET  orders · orders/{id}          POST orders {items, mode: wallet|counter|upi, utr?, tableId?, note?}
POST orders/{id}/pay               (settle an unpaid bill from wallet)
GET  wallet                        POST dues/pay {amount?}   POST wallet/topup-requests {amount, utr}
GET  frames · rankings · activity  POST activity/seen
GET  bookings · bookings/availability?tableId&date   POST bookings   DELETE bookings/{id}
GET  tournaments                   POST/DELETE tournaments/{id}/join {mode: wallet|counter}
GET  membership-plans              POST membership-plans/{id}/buy {mode: wallet}
POST membership-requests {planId, utr}   GET requests
PATCH profile {name?, phone?}      POST switch-club {clubId}
```

## Owner API (`/api/clubs/{club_id}/portal`)

```
GET  topup-requests?status=pending|approved|rejected|all
POST topup-requests/{id}/approve {mode: upi|cash|card, note?}     POST …/reject {reason}
GET  membership-requests?status=…
POST membership-requests/{id}/approve {mode}                       POST …/reject {reason}
GET  orders?status=open|all           (kitchen / counter queue of app orders)
POST orders/{id}/fulfilment {status: placed|preparing|ready|served}
```
Approvals book the sale through `payment_log` / `grant_plan` exactly like a
counter sale. Cancelling an app order = `DELETE /item-bills/{id}` (stock and
wallet/due are restored by the existing bill logic).

## Data added

* `menu_items`: optional `kind` (`cafe`|`shop`), `description`, `featured`,
  `image`. Without `kind`, the category name decides (Snacks/Beverages/… → cafe).
* `item_bills` from the app: `source: "member_order"`, `channel`, `deliverTo`,
  `requestedMode`, `fulfilment`, `paymentRef` + `verification` for UPI.
* New collections: `wallet_topup_requests`, `membership_requests`, `login_otps`.
* `clubs.upiId` (or `settings.upiId`) — shown for UPI payments (deep link on
  phones + copyable ID); `clubs.qrCode` is shown too when present.
* `users.activitySeenAt` — unread badge.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `MEMBERS_WEB_ENABLED` | `true` | mount `/members` |
| `MEMBERS_WEB_LOGIN_OTP` | `true` in prod, `false` in `AUTH_DEV_MODE` | require an emailed 6-digit code (needs SMTP; dev shows the code on screen) |
| `MEMBERS_WEB_GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_ID` | — | enables the Google button |
| `MEMBERS_WEB_TABLES_POLL_MS` | `20000` | live-table refresh |
| `MEMBERS_WEB_ORDERS_POLL_MS` | `45000` | order-status refresh |
| `MEMBERS_WEB_CLUB_TIMEZONE` | `Asia/Kolkata` | display timezone |

## Adding photos to your real items

Set `image` when creating or patching a menu item through
`/api/clubs/{club_id}/menu-items` or `/api/clubs/{club_id}/menu-items/{item_id}`.
It may be a hosted URL, a path under `/static/…`, or a base64
`data:image/jpeg;base64,…` string (up to 2.5 MB). The menu-item list and
consolidated club-data response retain the image value; the member catalogue
serves data-URL images through the cached `/members/img/{item_id}` route.
Items without an image get a coloured emoji tile.

## Demo data

`scripts/seed_demo.py` seeds Rowdy's Den with 3 tables (one live session),
12 cafe items and 9 pro-shop items with photos, a fortnight of frames, app and
counter orders, bookings, plan sales, a pending UPI membership request, an
approved top-up and an upcoming tournament. Ravi has ₹500 in his wallet and no
dues; Arjun has ₹170 due and 6 frames left on his pass.
