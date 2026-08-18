"""JWT auth (HS256 + issuer), Google sign-in verification, role/tenant guards."""
from datetime import timedelta

import jwt
from fastapi import Depends, Header, HTTPException

from .config import settings
from .db import get_db
from .util import now_utc, now_iso


def issue_token(user_id: str) -> str:
    now = now_utc()
    payload = {
        "sub": user_id,
        "iss": settings.JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.JWT_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def verify_google_token(id_token: str) -> dict:
    """Verify a Google ID token signature. RS256 via JWKS.

    Audience must match ANY configured client ID — the web client (React site)
    plus any extras in GOOGLE_CLIENT_IDS (e.g. the Flutter app's clients).
    """
    audiences = settings.google_audiences
    if not audiences:
        raise HTTPException(400, "Google sign-in is not configured on the server")
    try:
        jwks = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
        key = jwks.get_signing_key_from_jwt(id_token)
        data = jwt.decode(
            id_token, key.key, algorithms=["RS256"],
            audience=audiences,
            issuer=["https://accounts.google.com", "accounts.google.com"],
        )
    except Exception:
        raise HTTPException(401, "Google sign-in could not be verified")
    if not data.get("email") or not data.get("email_verified"):
        raise HTTPException(401, "Google account needs a verified email")
    return data


def is_master_email(email: str) -> bool:
    return (email or "").strip().lower() in settings.MASTER_ADMIN_EMAILS


async def upsert_user(db, email: str, name: str = "", picture: str = "", google_sub: str = "",
                      phone: str = "", location: str = "") -> dict:
    from .util import uid
    email = email.strip().lower()
    user = await db.users.find_one({"email": email})
    update = {"lastLoginAt": now_iso()}
    if name:
        update["name"] = name
    if picture:
        update["picture"] = picture
    if google_sub:
        update["googleSub"] = google_sub
    if is_master_email(email):
        update["role"] = "master"
    if user:
        await db.users.update_one({"id": user["id"]}, {"$set": update})
        user.update(update)
        return user
    user = {
        "id": uid("u"), "email": email, "name": name or email.split("@")[0].title(),
        "picture": picture or "", "phone": phone or "", "location": location or "",
        "role": "master" if is_master_email(email) else "owner",
        "active": True, "clubIds": [], "subscription": None,
        "createdAt": now_iso(), "updatedAt": now_iso(), "lastLoginAt": now_iso(),
    }
    if google_sub:
        user["googleSub"] = google_sub
    await db.users.insert_one(user)
    return user


_LOCAL_ADMIN = {
    "id": "local-admin", "email": "admin@localhost", "name": "Local Admin",
    "picture": "", "role": "owner", "active": True, "clubIds": [],
    "phone": "", "location": "",
    "subscription": {"status": "active", "planName": "Local (auth disabled)"},
}


async def current_user(authorization: str = Header(default=None)):
    """401 on any auth failure — client clears session and shows login."""
    if settings.AUTH_DISABLED:
        return dict(_LOCAL_ADMIN)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization[7:].strip()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"],
                             issuer=settings.JWT_ISSUER)
    except Exception:
        raise HTTPException(401, "Your session expired — please sign in again")
    db = await get_db()
    user = await db.users.find_one({"id": payload.get("sub", "")})
    if not user:
        raise HTTPException(401, "Authentication required")
    if not user.get("active", True):
        raise HTTPException(403, "Account disabled — contact the Master Admin")
    return user


def is_master(user: dict) -> bool:
    return user.get("role") == "master" or is_master_email(user.get("email", ""))


async def master_user(user: dict = Depends(current_user)):
    if not is_master(user):
        raise HTTPException(403, "Master Admin access required")
    return user
