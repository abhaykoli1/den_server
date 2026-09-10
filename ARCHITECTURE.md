# Members App Architecture

## Before: Separate Processes

```
┌─────────────────────────────────────────────────────────────┐
│                    Client Browser                           │
│                   http://localhost:5173                      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ↓
              ┌──────────────────────────────┐
              │    den_mem_web/server.py     │  Port 5173
              │  (Proxy + Static Files)      │
              └──────────────────────────────┘
                            │
                            ↓ (proxies /api to)
              ┌──────────────────────────────┐
              │    den_server (FastAPI)      │  Port 8000
              │  - /api/auth/*               │
              │  - /api/member-portal/*      │
              │  - All backend logic         │
              └──────────────────────────────┘

❌ 2 processes to manage
❌ CORS configuration needed
❌ 2 different ports
❌ Complex deployment
```

## After: Integrated with Jinja2

```
┌─────────────────────────────────────────────────────────────┐
│                    Client Browser                           │
│                  http://localhost:8000/members               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ↓
        ┌───────────────────────────────────────────┐
        │    den_server (Single FastAPI App)       │  Port 8000
        ├───────────────────────────────────────────┤
        │  Route: /members                          │
        │  ├─ GET / → Jinja2 render base.html      │
        │  ├─ GET /app.js → serve app.js           │
        │  └─ GET /manifest.json → PWA manifest    │
        ├───────────────────────────────────────────┤
        │  Route: /api/*                            │
        │  ├─ /auth/* → authentication              │
        │  ├─ /member-portal/* → member data       │
        │  ├─ /clubs/* → club management           │
        │  └─ ... all existing endpoints           │
        └───────────────────────────────────────────┘
                            ↓
                    [mongomock/MongoDB]

✅ 1 process only
✅ NO CORS needed (same-origin)
✅ Single port
✅ Simple deployment
✅ Configuration via Jinja2
```

## How Jinja2 Configuration Works

```
Client Request: GET /members
       │
       ↓
┌─────────────────────────────────┐
│ FastAPI Handler                 │
│ (members_web.py)                │
└─────────────────────────────────┘
       │
       ↓
┌──────────────────────────────────────────┐
│ Read Configuration                       │
│ from app/config.py:                      │
│ ├─ MEMBERS_WEB_API_BASE                 │
│ ├─ MEMBERS_WEB_GOOGLE_CLIENT_ID         │
│ ├─ MEMBERS_WEB_CLUB_TIMEZONE            │
│ ├─ MEMBERS_WEB_TABLES_POLL_MS           │
│ └─ MEMBERS_WEB_ORDERS_POLL_MS           │
└──────────────────────────────────────────┘
       │
       ↓
┌──────────────────────────────────────────┐
│ Render Jinja2 Template                   │
│ (templates/members/base.html)            │
│                                          │
│ Inject config as variables:              │
│ {{ api_base }}                           │
│ {{ google_client_id }}                   │
│ {{ club_timezone }}                      │
│ {{ tables_poll_ms }}                     │
│ {{ orders_poll_ms }}                     │
└──────────────────────────────────────────┘
       │
       ↓
┌──────────────────────────────────────────┐
│ HTML Response with Inline Config:        │
│                                          │
│ <script>                                 │
│ window.DEN_CONFIG = {                    │
│   API_BASE: "/api",                      │
│   GOOGLE_CLIENT_ID: "...",               │
│   CLUB_TIMEZONE: "Asia/Kolkata",         │
│   TABLES_POLL_MS: 20000,                 │
│   ORDERS_POLL_MS: 45000                  │
│ };                                       │
│ </script>                                │
└──────────────────────────────────────────┘
       │
       ↓
Browser renders HTML
Client-side app.js reads window.DEN_CONFIG
App makes API calls to /api/*
```

## Configuration Sources (Priority Order)

```
1. RUNTIME ENVIRONMENT VARIABLES (Highest Priority)
   $ export MEMBERS_WEB_API_BASE=/api
   $ python3 run.py

2. .ENV FILE (Medium Priority)
   den_server/.env
   MEMBERS_WEB_API_BASE=/api

3. CODE DEFAULTS (Lowest Priority)
   app/config.py
   MEMBERS_WEB_API_BASE: str = os.environ.get(
       "MEMBERS_WEB_API_BASE", "/api"
   )
```

## Routing Layers

```
Application Level:
  /members              ← Main SPA
  /members/app.js       ← App logic
  /members/manifest.json ← PWA manifest

API Level:
  /api/auth/*                    ← Login, OAuth
  /api/member-portal/*           ← Dashboard, wallet, orders
  /api/clubs/*                   ← Club management
  /api/tables/*                  ← Live tables
  /api/sessions/*                ← Billing sessions
  ... (see backend routers)

Admin Level:
  /api/master/*                  ← Master admin endpoints
  /api/platform/*                ← Platform settings
  /docs                          ← Swagger API docs
  /health                        ← Health check
```

## Data Flow: API Call Example

```
User clicks "View Wallet"
       │
       ↓
app.js detects route change
       │
       ↓
Reads window.DEN_CONFIG.API_BASE  → "/api"
       │
       ↓
Makes fetch call:
GET /api/member-portal/wallet
with Authorization header (JWT token)
       │
       ↓
FastAPI processes at same origin
(den_server, port 8000)
       │
       ↓
Returns wallet data (balance, dues, transactions)
       │
       ↓
app.js renders wallet UI
```

## Key Advantages

| Aspect | Benefit |
|--------|---------|
| **Same Origin** | No CORS headers needed, secure by default |
| **Single Port** | Easier to deploy, manage, and monitor |
| **Jinja2 Templating** | Dynamic config injection, environment-aware |
| **No Build Step** | Template rendered at request time |
| **Centralized Config** | All settings in one place (app/config.py) |
| **Fallback Values** | Works with sensible defaults if not configured |
| **Environment-Aware** | Dev/staging/prod can have different configs |
| **PWA Support** | Can be installed as app, works offline |

## Performance Impact

```
Before (2 processes):
  Browser → HTTP → den_mem_web (proxy) → HTTP → den_server
                   ↑                    ↑
                   Overhead            Overhead

After (1 process):
  Browser → HTTP → den_server (both app + api)
                   ↑
                   Single hop
```

Network request time: Reduced by ~1 hop
Total startup time: Reduced (1 server instead of 2)
Memory usage: Reduced (shared process)
Maintenance: Simplified (1 codebase, 1 deployment)

## Environment-Specific Configuration

Development:
```
MONGODB_URI=mongomock://demo
AUTH_DEV_MODE=true
MEMBERS_WEB_API_BASE=/api
MEMBERS_WEB_GOOGLE_CLIENT_ID=          # Empty for testing
```

Staging:
```
MONGODB_URI=mongodb+srv://...staging...
AUTH_DEV_MODE=false
MEMBERS_WEB_API_BASE=https://api-staging.example.com/api
MEMBERS_WEB_GOOGLE_CLIENT_ID=your-staging-client-id
```

Production:
```
MONGODB_URI=mongodb+srv://...prod...
AUTH_DEV_MODE=false
MEMBERS_WEB_API_BASE=https://api.example.com/api
MEMBERS_WEB_GOOGLE_CLIENT_ID=your-prod-client-id
```

## Deployment Checklist

- [ ] Set `AUTH_DEV_MODE=false` in production
- [ ] Configure `GOOGLE_CLIENT_ID` for OAuth
- [ ] Set `MEMBERS_WEB_GOOGLE_CLIENT_ID` if different from web OAuth
- [ ] Configure `MONGODB_URI` to production cluster
- [ ] Set `CORS_ORIGINS` to your domain(s)
- [ ] Configure SMTP for email notifications
- [ ] Set `APP_PUBLIC_URL` to your domain
- [ ] Set `MEMBERS_WEB_ENABLED=true` (or omit, defaults to true)
- [ ] Test `/members` endpoint in production
- [ ] Verify API calls work same-origin
- [ ] Test PWA installation
- [ ] Monitor error logs

---

**For detailed info:** See `den_server/MEMBERS_WEB.md`
