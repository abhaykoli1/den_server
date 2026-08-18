"""Auth routes — Google sign-in, dev login (AUTH_DEV_MODE only), profile."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import (current_user, issue_token, is_master_email, upsert_user,
                    verify_google_token)
from ..config import settings
from ..models import DevLoginIn, GoogleIn, MePatchIn
from ..util import now_iso, uid
from .clubs import DEFAULT_SETTINGS

router = APIRouter(prefix="/auth", tags=["auth"])


async def _master_starter_club(db, user: dict):
    """MASTER_ADMIN_EMAILS wale har account ka ek starter club pehle se ready ho —
    pehli baar login pe auto-create (idempotent: sirf jab 0 clubs owned)."""
    if user.get("id") == "local-admin" or not is_master_email(user.get("email", "")):
        return
    if await db.clubs.count_documents({"ownerUserId": user["id"]}):
        return
    club = {"id": uid("club"), "name": "Rowdy's Den", "logo": "",
            "ownerUserId": user["id"], "settings": dict(DEFAULT_SETTINGS),
            "createdAt": now_iso()}
    await db.clubs.insert_one(club)
    await db.users.update_one({"id": user["id"]},
                              {"$addToSet": {"clubIds": club["id"]}})
    user["clubIds"] = (user.get("clubIds") or []) + [club["id"]]


@router.post("/google")
async def google_login(payload: GoogleIn):
    token = (payload.idToken or payload.credential or "").strip()
    if len(token) < 10:
        raise HTTPException(
            400, "idToken (Google credential) is required — "
            "frontend should POST {idToken: credentialResponse.credential}")
    g = verify_google_token(token)
    db = await db_mod.get_db()
    user = await upsert_user(db, g["email"], g.get("name", ""), g.get("picture", ""),
                             google_sub=g.get("sub", ""))
    await _master_starter_club(db, user)
    return {"user": user, "token": issue_token(user["id"])}


@router.post("/dev")
@router.post("/dev-login")  # web app alias — dono route same handler
async def dev_login(payload: DevLoginIn):
    if settings.AUTH_DISABLED:
        raise HTTPException(400, "Auth is disabled on this server")
    if not settings.AUTH_DEV_MODE:
        raise HTTPException(403, "Dev login is disabled")
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "That doesn't look like a valid email")
    db = await db_mod.get_db()
    user = await upsert_user(db, email, payload.name or "", phone=payload.phone or "",
                             location=payload.location or "")
    await _master_starter_club(db, user)
    return {"user": user, "token": issue_token(user["id"])}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return user


@router.patch("/me")
async def patch_me(payload: MePatchIn, user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    ops = {}
    if payload.name is not None:
        ops["name"] = payload.name.strip()
    if payload.phone is not None:
        ops["phone"] = payload.phone.strip()
    if payload.location is not None:
        ops["location"] = payload.location.strip()
    if not ops:
        raise HTTPException(400, "Nothing to update")
    ops["updatedAt"] = now_iso()
    if user["id"] == "local-admin":
        user.update(ops)
        return user
    user = await db.users.find_one_and_update({"id": user["id"]}, {"$set": ops})
    return user


@router.get("/dev-mode")
async def dev_mode():
    """Lets clients decide whether to show the dev-login form (not in the spec's 62
    routes count; harmless public flag)."""
    return {"devMode": settings.AUTH_DEV_MODE and not settings.AUTH_DISABLED,
            "googleClientId": bool(settings.GOOGLE_CLIENT_ID)}
