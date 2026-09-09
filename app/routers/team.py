"""Club Staff (Admin ▸ team) — owner-only. Masters are NEVER listed."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user, is_master
from ..models import TeamStaffIn
from ..services import deny_staff_admin
from ..util import now_iso, uid

router = APIRouter(prefix="/team", tags=["team"])


def _handler_entry(u: dict, is_owner: bool) -> dict:
    return {
        "id": u.get("id"), "name": u.get("name"), "email": u.get("email"),
        "role": "owner" if is_owner else u.get("role", "staff"),
        "picture": u.get("picture", ""), "active": u.get("active", True),
        "lastLoginAt": u.get("lastLoginAt"), "isOwner": is_owner,
    }


@router.get("")
async def team_overview(user: dict = Depends(current_user)):
    deny_staff_admin(user)
    db = await db_mod.get_db()
    if is_master(user):
        clubs = await db.clubs.find({}).to_list(None)
    else:
        owned = await db.clubs.find({"ownerUserId": user["id"]}).to_list(None)
        member = await db.clubs.find({"id": {"$in": user.get("clubIds") or []}}).to_list(None)
        seen = {c["id"] for c in owned}
        clubs = owned + [c for c in member if c["id"] not in seen]
    out = []
    all_users = await db.users.find({}).to_list(None)
    for club in clubs:
        handlers = []
        owner = next((u for u in all_users if u["id"] == club.get("ownerUserId")), None)
        if owner and owner.get("role") != "master":
            handlers.append(_handler_entry(owner, True))
        for u in all_users:
            if u.get("role") == "master" or u["id"] == club.get("ownerUserId"):
                continue  # masters NEVER listed
            if club["id"] in (u.get("clubIds") or []):
                handlers.append(_handler_entry(u, False))
        out.append({"club": {"id": club["id"], "name": club["name"],
                             "logo": club.get("logo", "")},
                    "handlers": handlers, "staff": handlers})  # staff = web alias
    return {"clubs": out, "rows": out}  # rows = web alias


@router.post("/staff")
async def create_staff(payload: TeamStaffIn, user: dict = Depends(current_user)):
    deny_staff_admin(user)
    db = await db_mod.get_db()
    email = payload.email.strip().lower()
    if user.get("role") == "master":
        raise HTTPException(403, "Master accounts cannot create club staff here")

    club_query = {"ownerUserId": user["id"]}
    if payload.clubId:
        club_query["id"] = payload.clubId
    club = await db.clubs.find_one(club_query)
    if not club:
        raise HTTPException(404, "Create a club before adding staff")

    existing = await db.users.find_one({"email": email})
    if existing and existing.get("role") not in ("staff",):
        raise HTTPException(400, "This email already belongs to an owner or master account")

    club_ids = list(dict.fromkeys((existing or {}).get("clubIds") or []))
    if club["id"] not in club_ids:
        club_ids.append(club["id"])

    if existing:
        updated = await db.users.find_one_and_update(
            {"id": existing["id"]},
            {"$set": {"name": payload.name.strip(), "clubIds": club_ids,
                      "active": True, "updatedAt": now_iso()}},
        )
    else:
        updated = {
            "id": uid("u"), "email": email, "name": payload.name.strip(),
            "picture": "", "phone": "", "location": "", "role": "staff",
            "active": True, "clubIds": [club["id"]], "subscription": None,
            "createdAt": now_iso(), "updatedAt": now_iso(), "lastLoginAt": None,
        }
        await db.users.insert_one(updated)
    updated.pop("_id", None)
    return {"ok": True, "user": updated, "club": {"id": club["id"], "name": club["name"]}}
