# Google OAuth Setup Guide for Rowdy's Den

## Overview

The members app supports **Google Sign-In** for seamless authentication. Users can log in with their Google account instead of entering a password.

## Step 1: Create Google OAuth Credentials

### 1.1 Go to Google Cloud Console

1. Visit: https://console.cloud.google.com/
2. Sign in with your Google account
3. Create a new project (or select existing):
   - Click the project dropdown at the top
   - Click "NEW PROJECT"
   - Name it: "Rowdy's Den Members" (or whatever you prefer)
   - Click CREATE

### 1.2 Enable Google Identity Services API

1. In the Cloud Console, go to APIs & Services → Library
2. Search for "Google Identity Services"
3. Click on "Google Identity Services"
4. Click "ENABLE"

### 1.3 Create OAuth Consent Screen

1. Go to APIs & Services → OAuth consent screen
2. Select "External" user type
3. Click CREATE
4. Fill in the form:
   - **App name**: Rowdy's Den Members
   - **User support email**: your-email@gmail.com
   - **Developer contact**: your-email@gmail.com
5. Click SAVE AND CONTINUE through all sections
6. On the final page, click BACK TO DASHBOARD

### 1.4 Create OAuth Client Credentials

1. Go to APIs & Services → Credentials
2. Click "CREATE CREDENTIALS" → "OAuth client ID"
3. Select **"Web application"**
4. Under "Authorized redirect URIs", add:
   ```
   http://localhost:8000
   http://127.0.0.1:8000
   https://yourdomain.com          (your production domain)
   ```
5. Click CREATE
6. A dialog will show your credentials:
   - **Client ID** (copy this)
   - **Client Secret** (optional for web app)

## Step 2: Configure Rowdy's Den Backend

### 2.1 Local Development (.env file)

Create or update `den_server/.env`:

```bash
# Google OAuth Web Client (main backend)
GOOGLE_CLIENT_ID=YOUR_CLIENT_ID_HERE.apps.googleusercontent.com

# Members Web App (can be same or different)
MEMBERS_WEB_GOOGLE_CLIENT_ID=YOUR_CLIENT_ID_HERE.apps.googleusercontent.com
```

### 2.2 Production (Vercel / Hosting Platform)

Set environment variables in your hosting dashboard:

**Vercel:**
1. Go to Project Settings → Environment Variables
2. Add:
   - Name: `GOOGLE_CLIENT_ID`
   - Value: `YOUR_CLIENT_ID_HERE.apps.googleusercontent.com`
   - Add to: Production, Preview, Development (select all)
3. Add:
   - Name: `MEMBERS_WEB_GOOGLE_CLIENT_ID`
   - Value: `YOUR_CLIENT_ID_HERE.apps.googleusercontent.com`
   - Add to: Production, Preview, Development (select all)
4. Click Save
5. Redeploy your project

**Other Platforms:**
Set the environment variables before starting the server:
```bash
export GOOGLE_CLIENT_ID="YOUR_CLIENT_ID_HERE.apps.googleusercontent.com"
export MEMBERS_WEB_GOOGLE_CLIENT_ID="YOUR_CLIENT_ID_HERE.apps.googleusercontent.com"
python3 run.py
```

## Step 3: Test Google Login

### 3.1 Start the Backend

```bash
cd /Users/santa/Workspace/den/den_server
pip install -r requirements.txt
MONGODB_URI="mongomock://demo" AUTH_DEV_MODE=false python3 run.py
```

### 3.2 Open the App

```
http://localhost:8000/members
```

### 3.3 Look for Google Button

On the login screen, you should see:
- Email sign-in field
- **"Sign in with Google"** button

### 3.4 Test Login

1. Click "Sign in with Google"
2. You'll be redirected to Google's login page
3. Enter your Google credentials
4. Authorize the app to access your email
5. You'll be logged in and redirected to the home screen

## Troubleshooting

### "Google button not showing"

**Cause**: `MEMBERS_WEB_GOOGLE_CLIENT_ID` is empty or not set

**Fix**:
```bash
# Check your .env file
cat den_server/.env | grep GOOGLE

# Should show:
# MEMBERS_WEB_GOOGLE_CLIENT_ID=...apps.googleusercontent.com

# If empty, restart the server with:
MEMBERS_WEB_GOOGLE_CLIENT_ID="your-id" python3 run.py
```

### "Invalid OAuth Client ID"

**Cause**: Client ID not added to Google Cloud Console authorized list

**Fix**:
1. Go to Google Cloud Console → Credentials
2. Click your OAuth 2.0 Client ID
3. Add your localhost and domain to "Authorized redirect URIs"
4. Save
5. Restart the server

### "CORS or redirect_uri_mismatch error"

**Cause**: Your domain not listed in Google Cloud Console

**Fix**:
1. Go to Google Cloud Console → Credentials
2. Edit your OAuth 2.0 Client ID
3. Add your domain to Authorized redirect URIs:
   ```
   https://yourdomain.com
   https://yourdomain.com/members
   ```
4. Click SAVE
5. Wait 1-2 minutes for changes to propagate

### "Cross-Origin Request Blocked"

**Cause**: CORS not configured

**Fix**: Make sure in `den_server/.env`:
```bash
CORS_ORIGINS=http://localhost:8000,https://yourdomain.com
```

## Multiple OAuth Clients (Optional)

You can have different OAuth clients for different purposes:

```bash
# Main backend (used by React admin dashboard)
GOOGLE_CLIENT_ID=web-client-id.apps.googleusercontent.com

# Members mobile web app (can be same or different)
MEMBERS_WEB_GOOGLE_CLIENT_ID=members-client-id.apps.googleusercontent.com

# Flutter app (iOS)
GOOGLE_CLIENT_IDS=flutter-ios-client-id.apps.googleusercontent.com,flutter-android-client-id.apps.googleusercontent.com
```

All will work with the backend's `/api/auth/google` endpoint.

## Authentication Flow

```
1. User clicks "Sign in with Google" button
                   ↓
2. Browser loads Google Identity Services (gsi/client.js)
                   ↓
3. User enters Google credentials (or already signed in)
                   ↓
4. Google returns ID Token to app.js
                   ↓
5. app.js POST /api/auth/google { idToken: "..." }
                   ↓
6. Backend verifies token signature with Google
                   ↓
7. Backend creates/updates user in MongoDB
                   ↓
8. Backend returns JWT token
                   ↓
9. App stores JWT in localStorage
                   ↓
10. User is logged in!
```

## Security Best Practices

1. **Never expose Client Secret**
   - Only use Client ID (public)
   - Keep Client Secret on backend only

2. **Use HTTPS in Production**
   - Google won't return credentials over HTTP
   - Dev (localhost) is exempted

3. **Verify ID Tokens**
   - Backend uses Google's public keys
   - No need to send Client Secret

4. **Restrict Redirect URIs**
   - Only add domains you control
   - Use exact URLs, not wildcards

5. **Keep Client ID in Config**
   - Don't hardcode in frontend
   - Inject via Jinja2 template
   - Easy to switch between dev/prod

## API Endpoint

**POST /api/auth/google**

Request:
```json
{
  "idToken": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjEifQ..."
}
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "mem_123abc",
    "email": "user@example.com",
    "name": "User Name"
  }
}
```

## Next Steps

1. ✅ Create OAuth credentials
2. ✅ Set `MEMBERS_WEB_GOOGLE_CLIENT_ID` in .env
3. ✅ Test on localhost
4. ✅ Deploy to production
5. ✅ Update Google Console with production domain
6. ✅ Test in production

---

**Questions?** Check:
- Google OAuth docs: https://developers.google.com/identity/gsi/web
- Backend auth code: `den_server/app/auth.py`
- Frontend login code: `den_server/app/routers/app.js` (search "initGoogle")
