# Google sign-in verification checklist

## Implementation

- [x] Premium Jinja2 login page has a Google sign-in area.
- [x] GSI widget renders when `MEMBERS_WEB_GOOGLE_CLIENT_ID` or
      `GOOGLE_CLIENT_ID` is configured.
- [x] Backend verifies Google signature, issuer, audience and
      `email_verified` through `verify_google_token`.
- [x] Verified email is matched to a member profile before session creation.
- [x] Session is the existing `den_member` HttpOnly cookie/JWT flow.
- [x] Owners and staff cannot use the member portal.
- [x] Dev-only fallback chooser is available only when `AUTH_DEV_MODE=true`
      and no real Client ID is configured.
- [x] Login error paths render the complete page, including the Google area.

## Files to inspect

```text
app/auth.py                         verify_google_token, member lookup
app/config.py                       Google client IDs and auth flags
app/routers/members_web.py          page context and /members/login/google
templates/members/login.html        GSI widget / dev fallback button
templates/members/base.html         shared CSS and JS boot data
```

## Local smoke check

```bash
./start-members-app.sh --fresh
curl -s http://localhost:8000/members/login | grep -E \
  'g_id_signin|Continue with Google|google-demo'
```

With a configured Client ID, the source contains `g_id_signin` and the GSI
script. Without one in dev mode, it contains `data-google-demo` and the
`/members/login/google-demo` flow.

## Google Cloud checklist

- [ ] OAuth consent screen created and published/test users added.
- [ ] Web OAuth Client ID created.
- [ ] Local browser origin added, for example `http://localhost:8000`.
- [ ] Production HTTPS origin added exactly, with no path or wildcard.
- [ ] Preview/sandbox origin added separately if that is where the app is run.
- [ ] `MEMBERS_WEB_GOOGLE_CLIENT_ID` set in the deployment environment.
- [ ] `AUTH_DEV_MODE=false` in production.
- [ ] A test Google email is present in the club's `members` collection.

## Security notes

The Client ID is safe to expose to the browser; a Client Secret is not. The
backend must verify the ID token and must not trust a browser-submitted email.
The member app uses the verified email only to find an existing member. UPI,
wallet, orders, bookings and all other mutations remain behind the Bearer JWT
member API.
