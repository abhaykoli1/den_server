# Google sign-in setup for Rowdy's Den Members App

This app uses Google Identity Services on the Jinja2 `/members/login` page.
The browser receives a Google ID-token credential; the backend verifies it and
then creates the usual `den_member` session.

## 1. Create a Web OAuth client

In [Google Cloud Console](https://console.cloud.google.com/):

1. Create/select a project.
2. Configure the OAuth consent screen. Use an app name such as **Rowdy's Den
   Members**, add a support email and developer contact, and publish or add
   test users as required.
3. Create **Credentials → OAuth client ID → Web application**.
4. Add the browser origins under **Authorized JavaScript origins**, for example:

   ```text
   http://localhost:8000
   http://127.0.0.1:8000
   https://members.example.com
   ```

   Add the exact origin used by every production/preview deployment. Do not
   include a path here. Do not add a Client Secret to the app.

## 2. Configure the server

In `den_server/.env` or the hosting environment:

```env
MEMBERS_WEB_GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
# Optional shared fallback:
GOOGLE_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
```

`MEMBERS_WEB_GOOGLE_CLIENT_ID` takes precedence for the members page. The
backend verifier accepts it together with `GOOGLE_CLIENT_ID` and the IDs in
`GOOGLE_CLIENT_IDS`.

Restart:

```bash
./start-members-app.sh                 # demo mode
./start-members-app.sh --prod          # real Mongo/JWT/SMTP from .env
```

Open `http://localhost:8000/members`. The login page should show the Google
Identity Services pill button above the email sign-in form.

## 3. Login endpoint and flow

The GSI `login_uri` is:

```text
https://YOUR_ORIGIN/members/login/google
```

`app/routers/members_web.py` accepts the posted credential and calls
`verify_google_token`. Verification checks:

- cryptographic signature using Google's public keys
- issuer (`accounts.google.com`)
- configured audience/client ID
- `email_verified`

The verified email is then matched to an existing club member. The resulting
JWT is placed in the `den_member` HttpOnly cookie and the member is redirected
to `/members`.

## 4. Local demo fallback

If `AUTH_DEV_MODE=true` and no Google Client ID is configured, the page shows a
clearly labelled demo Google chooser. It can select the seeded Ravi/Arjun
accounts or create a demo member for an entered email. This is only a UI/demo
convenience; `/members/login/google-demo` returns 403 outside dev mode.

The demo script can still show the real GSI widget if `.env` already contains a
Client ID. Remove/override the local ID to preview the fallback chooser.

## 5. Troubleshooting

### Button is missing

Check:

```bash
grep -E 'GOOGLE_CLIENT_ID|MEMBERS_WEB_GOOGLE_CLIENT_ID' .env
curl -s http://localhost:8000/members/login | grep -E 'g_id_signin|data-google-demo'
```

Restart after changing `.env`.

### `origin is not allowed`

Add the exact scheme, host and port shown in the browser to Authorized
JavaScript origins. A sandbox preview hostname is a different origin from
`localhost`.

### Google verifies but access is refused

The verified email must already be present in the club `members` data. Ask the
owner/counter to add the email to the member profile.

## Production checklist

- [ ] `AUTH_DEV_MODE=false`
- [ ] HTTPS enabled
- [ ] exact production origin configured in Google Cloud
- [ ] `MEMBERS_WEB_GOOGLE_CLIENT_ID` set in deployment secrets
- [ ] long random `JWT_SECRET`
- [ ] no Client Secret, SMTP password or Mongo credential committed to source
- [ ] test Google email linked to a member before launch
