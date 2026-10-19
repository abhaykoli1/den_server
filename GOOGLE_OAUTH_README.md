# Google sign-in · Members App

The members app has a real **Continue with Google** button implemented with
Google Identity Services (GSI). The UI is server-rendered by Jinja2; Google
returns an ID-token credential to `POST /members/login/google`, where the
backend verifies it before issuing the normal `den_member` session cookie. A
local styled fallback button stays visible until GSI renders, so sandboxed
previews that block external scripts still show the login action.

## Enable it

Set one of these environment variables before starting the server:

```env
MEMBERS_WEB_GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
# GOOGLE_CLIENT_ID is also accepted as the shared fallback.
```

The existing backend verifier accepts `GOOGLE_CLIENT_ID` plus any IDs in
`GOOGLE_CLIENT_IDS` as valid token audiences.

For Google Cloud Console:

1. Create a **Web application** OAuth client.
2. Configure the OAuth consent screen and add the club's support/developer
   contact.
3. Add every production hostname as an **Authorized JavaScript origin**. Add
   localhost origins for local development.
4. Keep the Client ID in environment configuration. A web Client ID is public;
   never put a Client Secret in templates or JavaScript.
5. Restart the app after changing the environment.

GSI uses the login URI below when configured in the template:

```text
https://your-domain.example/members/login/google
```

The exact origin/host must be allowed in Google Cloud Console. For a local
preview, use the URL shown by the one-click script (usually
`http://localhost:8000`).

## Flow

```text
/member login page
  → Google Identity Services pill button
  → Google credential POST /members/login/google
  → verify signature, issuer, audience and email_verified
  → match verified email to a member profile
  → issue the same member JWT/session as email login
  → redirect to /members
```

Only an email already linked to a member profile is accepted in production.
Owners and staff are redirected to the billing app rather than receiving a
member session.

## Local demo

```bash
./start-members-app.sh --fresh
# open http://localhost:8000/members
```

`AUTH_DEV_MODE=true` and an empty Google Client ID expose a clearly labelled
**demo Google** chooser so the UI can be previewed without a Google account.
The demo endpoint is `/members/login/google-demo` and returns `403` when dev
mode is disabled. It is not a production authentication method.

The one-click demo may still show the real GSI button if `.env` supplies a
Client ID; this is expected. Use the real button with an authorised origin, or
remove the ID from the local environment to preview the demo chooser.

## Files

- `templates/members/login.html` — real GSI widget or dev demo button
- `app/routers/members_web.py` — login context, Google credential endpoint,
  session cookie and dev-only demo endpoint
- `app/auth.py` — Google JWT verification and member lookup
- `app/config.py` — `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_IDS`,
  `MEMBERS_WEB_GOOGLE_CLIENT_ID`
- `templates/members/base.html` — module boot data and static assets
- `ARCHITECTURE.md` — complete request, trust-boundary and extension model

## Troubleshooting

### Button is not visible

Check the rendered page source for `g_id_signin`. If it is absent, set
`MEMBERS_WEB_GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_ID` and restart. In dev mode
with no client ID, the fallback demo button is shown instead.

### `origin is not allowed` or `redirect_uri_mismatch`

Add the exact browser origin to the Google OAuth client's Authorized JavaScript
origins, including scheme and port for local development. Do not add only an
API path and do not use `localhost` when testing a different preview hostname.

### Google succeeds but member access is refused

The verified Google email must match `members.email` (case-insensitive). Add
that email to the club member record first, then retry.

### Production checklist

- `AUTH_DEV_MODE=false`
- `MEMBERS_WEB_LOGIN_OTP=true` unless another approved sign-in policy is used
- HTTPS enabled
- exact production origins configured in Google Cloud Console
- `JWT_SECRET` set to a long random value
- no SMTP, Mongo or OAuth secrets committed to source control
