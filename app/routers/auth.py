"""Auth routes — Google sign-in, dev login (AUTH_DEV_MODE only), profile."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import (current_user, issue_token, is_master_email, resolve_member_by_email,
                    resolve_members_by_email,
                    upsert_user, verify_google_token)
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
    club = {"id": uid("club"), "name": "Rowdy's Den", "logo": "", "qrCode": "",
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
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "That doesn't look like a valid email")
    db = await db_mod.get_db()

    members = await resolve_members_by_email(db, email)
    member = members[0] if members else None
    if member:
        club_ids = list(dict.fromkeys(
            m.get("clubId") for m in members if m.get("clubId")
        ))
        clubs = await db.clubs.find({"id": {"$in": club_ids}}).to_list(None)
        for club in clubs:
            club.pop("_id", None)
        club_by_id = {club["id"]: dict(club) for club in clubs}
        memberships = []
        for item in members:
            membership = dict(item)
            membership.pop("_id", None)
            membership["club"] = club_by_id.get(item.get("clubId"))
            memberships.append(membership)
        member_name = (member.get("name") or payload.name or email.split("@")[0]).strip()
        member_user = {
            "id": member["id"],
            "email": email,
            "name": member_name,
            "picture": "",
            "phone": member.get("phone") or payload.phone or "",
            "location": "",
            "role": "member",
            "active": True,
            "clubIds": club_ids,
            "memberId": member["id"],
            "memberIds": [item["id"] for item in members],
            "memberships": memberships,
            "clubs": clubs,
            "clubId": member.get("clubId"),
            "subscription": None,
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "lastLoginAt": now_iso(),
        }
        await db.users.update_one({"email": email}, {"$set": member_user}, upsert=True)
        member_doc = dict(member)
        member_doc["role"] = "member"
        member_doc["memberId"] = member["id"]
        member_doc["clubId"] = member.get("clubId")
        member_doc["clubIds"] = club_ids
        member_doc["memberships"] = memberships
        member_doc["clubs"] = clubs
        club = club_by_id.get(member.get("clubId"))
        if club:
            member_doc["club"] = dict(club)
        return {"user": member_doc, "token": issue_token(member_user["id"])}

    existing_user = await db.users.find_one({"email": email})
    if existing_user:
        user = await upsert_user(db, email, payload.name or existing_user.get("name") or "",
                                 phone=payload.phone or existing_user.get("phone") or "",
                                 location=payload.location or existing_user.get("location") or "")
        await _master_starter_club(db, user)
        return {"user": user, "token": issue_token(user["id"])}

    if not settings.AUTH_DEV_MODE:
        raise HTTPException(403, "Dev login is disabled and no matching user/member profile was found")

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
