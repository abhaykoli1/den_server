# Rowdy's Den — Members Web App (Jinja2 Integration)

Members app has been **fully integrated into `den_server`** using Jinja2 templates. No separate process needed!

## What Changed

Previously:
- Standalone app running on separate port (5173) via `den_mem_web/server.py`
- Static files served separately

Now:
- Integrated directly into `den_server` on `/members` route
- Jinja2 templates with injected configuration
- Single FastAPI process handles both API and web UI
- Zero CORS setup needed (same-origin)

## Quick Start

### 1. Start the Backend (den_server)

```bash
cd /Users/santa/Workspace/den/den_server

# Install dependencies (if not already done)
pip install -r requirements.txt

# Fresh demo data (optional)
rm -f .devdata/snapshot.json

# Start the server
# With dev auth + in-memory MongoDB:
MONGODB_URI="mongomock://demo" AUTH_DEV_MODE=true PORT=8000 python3 run.py

# OR with production MongoDB + Google auth:
# PORT=8000 python3 run.py
```

The server starts on **http://0.0.0.0:8000**

### 2. Open the Members App

Simply open:
```
http://localhost:8000/members
```

That's it! No separate Python server, no CORS issues, no extra configuration needed.

## Configuration

### Environment Variables

Add these to your `.env` file in `den_server/` to customize the members app:

```bash
# Members Web App Configuration
MEMBERS_WEB_API_BASE=/api                              # API endpoint (usually same-origin)
MEMBERS_WEB_GOOGLE_CLIENT_ID=<your-client-id>         # Google OAuth (optional but recommended)
MEMBERS_WEB_CLUB_TIMEZONE=Asia/Kolkata                # Timezone for display
MEMBERS_WEB_TABLES_POLL_MS=20000                       # Live tables refresh rate (ms)
MEMBERS_WEB_ORDERS_POLL_MS=45000                       # Orders refresh rate (ms)
MEMBERS_WEB_ENABLED=true                              # Enable/disable the web app
```

### Where to Set Configuration

1. **Local Development**: Create/edit `.env` in `den_server/`
   ```bash
   echo "MEMBERS_WEB_GOOGLE_CLIENT_ID=your-id-here" >> den_server/.env
   ```

2. **Production (Vercel)**: Set environment variables in Vercel dashboard
   - Go to: Project Settings → Environment Variables
   - Add each `MEMBERS_WEB_*` variable

3. **Production (Other hosting)**: Set OS environment variables before starting
   ```bash
   export MEMBERS_WEB_GOOGLE_CLIENT_ID="your-id-here"
   export MEMBERS_WEB_CLUB_TIMEZONE="Asia/Kolkata"
   python3 run.py
   ```

### Google OAuth Setup (Optional)

To enable "Sign in with Google" for your members:

**Quick Setup** (5 minutes):
```bash
# 1. Create OAuth credentials at https://console.cloud.google.com/
# 2. Copy your Client ID
# 3. Set in .env:
MEMBERS_WEB_GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com

# 4. Restart server
./start-members-app.sh

# 5. Login page will show "Sign in with Google" button
```

**Full Setup Guide**: See [GOOGLE_OAUTH_SETUP.md](./GOOGLE_OAUTH_SETUP.md)

**Quick Reference**: See [QUICK_GOOGLE_SETUP.txt](./QUICK_GOOGLE_SETUP.txt)

## Routing

The members app is served at:

| Route | Purpose |
|-------|---------|
| `/members` | Main app (Jinja2 template) |
| `/members/app.js` | App logic (JavaScript) |
| `/members/manifest.json` | PWA manifest |
| `/api/*` | All existing backend endpoints |

## Demo Logins

Same as before — use the existing backend demo:

| Email | Shows |
|-------|-------|
| `ravi@example.com` | Wallet member, live on Snooker 1, 1 food order |
| `arjun@example.com` | Carries a due + pending frame due (pay-due flow) |

## Running on a Custom Port

To run on a different port (e.g., 3000):

```bash
PORT=3000 python3 run.py
```

Then access at:
```
http://localhost:3000/members
```

## Running on a Custom Path

If you need to serve the app at a different path (e.g., `/app/members`), this requires updating:

1. Template `base.html` → routes in Jinja2
2. Router `members_web.py` → prefix
3. Manifest `scope` and `start_url`

Current setup: `/members` — changes require modifying `app/routers/members_web.py` prefix.

## Features

✅ Login (email + Google OAuth if configured)  
✅ Club picker for multi-club members  
✅ Live tables with elapsed timers  
✅ Wallet & balance management  
✅ Food ordering with menu search  
✅ Order history with status timeline  
✅ Activity feed with filters  
✅ Frames & match records  
✅ Member profile & settings  
✅ Light/dark theme toggle  
✅ Offline support (localStorage cache)  
✅ PWA installable  
✅ Mobile-optimized (430px design)

## Architecture

```
den_server/
├── app/
│   ├── main.py                    ← imports members_web router
│   ├── config.py                  ← MEMBERS_WEB_* settings
│   └── routers/
│       ├── members_web.py         ← handles /members routes
│       └── app.js                 ← app logic (copied from den_mem_web)
├── templates/
│   └── members/
│       └── base.html              ← Jinja2 template with config injection
└── run.py                         ← starts single FastAPI server
```

## Migration from Old Setup (den_mem_web)

If you were running the old separate server:

```bash
# OLD (don't do this anymore):
cd den_mem_web
python3 server.py

# NEW (just do this):
cd den_server
python3 run.py
# Then open http://localhost:8000/members
```

The old `den_mem_web` directory still exists but is no longer used. You can:
- Keep it for reference
- Delete it to save space
- Archive it in git

## Technical Details

### How Configuration Injection Works

1. User requests `/members`
2. `members_web.py` renders `base.html` with Jinja2
3. Configuration from `config.py` is passed as template variables
4. HTML output includes inline `<script>` with config in `window.DEN_CONFIG`
5. App JS reads `window.DEN_CONFIG` at runtime

This means:
- **No separate config.js file** needed
- **Configuration is environment-aware** (dev/prod can differ)
- **No client-side config files** to manage
- **Same-origin API calls** (no CORS needed)

### Why This Is Better

| Aspect | Old (den_mem_web) | New (Jinja2) |
|--------|-------------------|--------------|
| **Processes** | 2 (server.py + den_server) | 1 (den_server only) |
| **Configuration** | Static file + env vars | Dynamic Jinja2 rendering |
| **CORS** | Required | Not needed (same-origin) |
| **Deployment** | 2 services to manage | Single service |
| **Start time** | 2 servers to start | 1 server to start |
| **Maintainability** | Split across folders | Centralized |

## Troubleshooting

### "Template not found" error
- Ensure `templates/members/base.html` exists
- Check path: should be relative to `den_server/app/`

### "Jinja2 not found" error
- Jinja2 is included with FastAPI
- Make sure `from jinja2 import ...` works: `python3 -c "import jinja2; print(jinja2.__version__)"`

### Members app loads but API calls fail
- Ensure `/api/*` endpoints are accessible
- Check CORS_ORIGINS in config.py includes your frontend URL
- Verify `MEMBERS_WEB_API_BASE` is correct

### Dark mode / theme not working
- Check browser localStorage: `localStorage.getItem('theme')`
- Open DevTools → Application → Local Storage → look for `theme` key

### PWA not installable
- Manifest must be at `/members/manifest.json` ✓
- HTTPS required on production (development allows HTTP)
- Icons must be present in `public/icons/`

## API Endpoints Used

The members app talks to these existing backend endpoints:

- `POST /api/auth/login` — email sign-in
- `POST /api/auth/google` — Google OAuth
- `GET /api/member-portal/dashboard` — home screen data
- `GET /api/member-portal/wallet` — balance, transactions, dues
- `GET /api/member-portal/orders` — order history
- `GET /api/member-portal/activity` — activity feed
- `GET /api/member-portal/frames` — snooker frames & matches
- `GET /api/member-portal/menu` — food menu & items
- And more (see backend routers)

All endpoints are served from the same `den_server` FastAPI app.

## Next Steps

1. ✅ Integration complete
2. Test on mobile devices or mobile viewport (430px)
3. Configure Google OAuth client ID (optional)
4. Deploy to production
5. Share `/members` link with club members

---

**Questions?** Check the backend README at `den_server/README.md` or the original web app docs at `den_mem_web/README.md`.
