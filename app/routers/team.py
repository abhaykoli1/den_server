"""Club Staff (Admin ▸ team) — owner-only. Masters are NEVER listed."""
from fastapi import APIRouter, Depends

from .. import db as db_mod
from ..auth import current_user, is_master
from ..services import deny_staff_admin

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
