"""Club Staff (Admin ▸ team) — owners manage their clubs; masters manage all."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user, is_master
from ..models import TeamStaffIn, TeamStaffPatchIn
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
    master = is_master(user)
    if master:
        manageable_clubs = await db.clubs.find({}).to_list(None)
    elif user.get("role") == "owner":
        manageable_clubs = await db.clubs.find({"ownerUserId": user["id"]}).to_list(None)
    else:
        raise HTTPException(403, "Only an admin or Master Admin can create staff accounts")

    if not manageable_clubs:
        raise HTTPException(404, "Create a club before adding staff")
    if payload.clubId:
        club = next((c for c in manageable_clubs if c["id"] == payload.clubId), None)
        if not club:
            raise HTTPException(404, "That club is not available to your account")
    elif len(manageable_clubs) == 1:
        club = manageable_clubs[0]
    else:
        raise HTTPException(400, "Choose which club this staff account will manage")

    existing = await db.users.find_one({"email": payload.email})
    if existing and existing.get("role") not in ("staff",):
        raise HTTPException(400, "This email already belongs to an owner or master account")
    if existing and existing.get("active", True) is False:
        raise HTTPException(403, "This account is disabled by the Master Admin")

    staff_id = (existing or {}).get("id") or uid("u")
    club_ids = list(dict.fromkeys((existing or {}).get("clubIds") or []))
    if club["id"] not in club_ids:
        club_ids.append(club["id"])
    if existing:
        updated = await db.users.find_one_and_update(
            {"id": staff_id},
            {"$set": {"name": payload.name.strip(), "clubIds": club_ids,
                      "updatedAt": now_iso()}},
        )
    else:
        updated = {
            "id": staff_id, "email": payload.email, "name": payload.name,
            "picture": "", "phone": "", "location": "", "role": "staff",
            "active": True, "clubIds": [club["id"]], "subscription": None,
            "createdAt": now_iso(), "updatedAt": now_iso(), "lastLoginAt": None,
        }
        await db.users.insert_one(updated)
    updated.pop("_id", None)
    return {"ok": True, "user": updated, "club": {"id": club["id"], "name": club["name"]}}


@router.patch("/staff/{staff_id}")
async def update_staff(staff_id: str, payload: TeamStaffPatchIn,
                       user: dict = Depends(current_user)):
    deny_staff_admin(user)
    db = await db_mod.get_db()
    staff = await db.users.find_one({"id": staff_id, "role": "staff"})
    if not staff:
        raise HTTPException(404, "Staff account not found")

    if is_master(user):
        club = await db.clubs.find_one({"id": payload.clubId})
    elif user.get("role") == "owner":
        club = await db.clubs.find_one({"id": payload.clubId,
                                        "ownerUserId": user["id"]})
    else:
        raise HTTPException(403, "Only an admin or Master Admin can edit staff")
    if not club:
        raise HTTPException(404, "That club is not available to your account")

    await db.users.update_one(
        {"id": staff_id, "role": "staff"},
        {"$set": {"name": payload.name, "updatedAt": now_iso()},
         "$addToSet": {"clubIds": payload.clubId}},
    )
    updated = await db.users.find_one({"id": staff_id})
    updated.pop("_id", None)
    return {"ok": True, "user": updated}
