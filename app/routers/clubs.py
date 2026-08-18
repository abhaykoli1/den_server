"""Clubs — create (subscription-gated, maxClubs enforced), settings, data feed, stats."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user, is_master
from ..config import settings
from ..models import ClubIn, ClubPatchIn, ClubSettingsIn
from ..services import (alias_player_ids, bill_web_aliases, billing_gate,
                        club_stats, frame_web_aliases, get_club, public_member,
                        sweep_expired_memberships)
from ..util import now_iso, uid

router = APIRouter(prefix="/clubs", tags=["clubs"])

DEFAULT_SETTINGS = {
    "winnerBonus": 0, "dueLimit": 1000, "defaultAdvance": 0,
    "currency": "INR", "currencySymbol": "₹", "monthlyTableDiscount": 0,
}


@router.get("")
async def list_clubs(user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    if is_master(user):
        return await db.clubs.find({}).to_list(None)
    clubs = await db.clubs.find({"ownerUserId": user["id"]}).to_list(None)
    extra = await db.clubs.find({"id": {"$in": user.get("clubIds") or []}}).to_list(None)
    seen = {c["id"] for c in clubs}
    return clubs + [c for c in extra if c["id"] not in seen]


@router.post("", status_code=201)
async def create_club(payload: ClubIn, user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    if user["id"] == "local-admin":
        limit_ok = True
    else:
        sub = user.get("subscription") or {}
        status = sub.get("status")
        if user.get("role") == "staff":
            raise HTTPException(403, "Staff accounts can't create clubs")
        if status not in ("trial", "active"):
            raise HTTPException(402, "Subscription required — choose a plan first")
        owned = await db.clubs.count_documents({"ownerUserId": user["id"]})
        limit_ok = owned < int(sub.get("maxClubs") or 1)
        if not limit_ok:
            raise HTTPException(
                400, f"Your plan allows only {sub.get('maxClubs')} club(s) — upgrade to add more")
    club = {
        "id": uid("club"), "name": payload.name.strip(), "logo": "",
        "ownerUserId": user["id"], "settings": dict(DEFAULT_SETTINGS),
        "createdAt": now_iso(),
    }
    await db.clubs.insert_one(club)
    club.pop("_id", None)
    if user["id"] != "local-admin":
        await db.users.update_one({"id": user["id"]},
                                  {"$addToSet": {"clubIds": club["id"]}})
    return club


@router.get("/{club_id}")
async def get_one(club_id: str, user: dict = Depends(current_user)):
    return await get_club(user, club_id)


@router.patch("/{club_id}")
async def patch_club(club_id: str, payload: ClubPatchIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    if user.get("role") == "staff":
        raise HTTPException(403, "Only the owner can edit club details")
    db = await db_mod.get_db()
    ops = {}
    if payload.name is not None:
        ops["name"] = payload.name.strip()
    if payload.logo is not None:
        if len(payload.logo) > settings.MAX_LOGO_BYTES + 100000:
            raise HTTPException(400, "Logo is too large — keep it under ~1.5 MB")
        ops["logo"] = payload.logo
    if not ops:
        raise HTTPException(400, "Nothing to update")
    await db.clubs.update_one({"id": club_id}, {"$set": ops})
    club.update(ops)
    club.pop("_id", None)
    return club


@router.delete("/{club_id}")
async def delete_club(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    if not is_master(user) and club.get("ownerUserId") != user["id"]:
        raise HTTPException(403, "Only the club owner can delete a club")
    db = await db_mod.get_db()
    for coll in ("tables", "members", "plans", "sessions", "frames", "menu_items",
                 "item_bills", "membership_sales", "expenses", "tournaments", "logs"):
        await getattr(db, coll).delete_many({"clubId": club_id})
    await db.clubs.delete_one({"id": club_id})
    await db.users.update_many({}, {"$pull": {"clubIds": club_id}})
    return {"ok": True, "message": f"Club deleted · {club['name']}"}


@router.patch("/{club_id}/settings")
async def patch_settings(club_id: str, payload: ClubSettingsIn,
                         user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    ops = {f"settings.{k}": v for k, v in payload.model_dump().items() if v is not None}
    if not ops:
        raise HTTPException(400, "Nothing to update")
    await db.clubs.update_one({"id": club_id}, {"$set": ops})
    for key, val in ops.items():
        parts = key.split(".")
        club["settings"][parts[1]] = val
    club.pop("_id", None)
    return club


@router.get("/{club_id}/stats")
async def stats(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    return await club_stats(club)


@router.get("/{club_id}/data")
async def club_data(club_id: str, user: dict = Depends(current_user)):
    """Consolidated feed — sidebar stats and screens load from this one call.
    Also runs the idempotent membership-expiry sweep."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    await sweep_expired_memberships(club)
    cid = club["id"]
    tables = await db.tables.find({"clubId": cid}).to_list(None)
    tables.sort(key=lambda t: (t.get("sortOrder", 0), t.get("name", "")))
    members = [public_member(m) for m in await db.members.find({"clubId": cid}).to_list(None)]
    members.sort(key=lambda m: m.get("name", "").lower())
    plans = await db.plans.find({"clubId": cid}).to_list(None)
    sessions = await db.sessions.find({"clubId": cid}).to_list(None)
    frames = await db.frames.find({"clubId": cid}, sort=[("createdAt", -1)]).to_list(200)
    menu_items = await db.menu_items.find({"clubId": cid}).to_list(None)
    menu_items.sort(key=lambda i: (i.get("category", ""), i.get("name", "")))
    item_bills = await db.item_bills.find({"clubId": cid}, sort=[("createdAt", -1)]).to_list(200)
    expenses = await db.expenses.find({"clubId": cid}, sort=[("date", -1)]).to_list(200)
    sales = await db.membership_sales.find({"clubId": cid}, sort=[("createdAt", -1)]).to_list(200)
    logs = await db.logs.find({"clubId": cid}, sort=[("createdAt", -1)]).to_list(100)
    tournaments = await db.tournaments.find({"clubId": cid}, sort=[("date", -1)]).to_list(None)
    for doc in sessions + frames:
        alias_player_ids(doc)  # players pe id=pid — web winner selection ka fix
    for f in frames:
        frame_web_aliases(f)   # legacy frames pe web keys backfill
    for b in item_bills:
        bill_web_aliases(b)    # legacy item bills pe web keys backfill
    stats = await club_stats(club)
    club = dict(club)
    club.pop("_id", None)
    return {
        "club": club, "tables": tables, "members": members, "plans": plans,
        "sessions": sessions, "frames": frames, "menuItems": menu_items,
        "itemBills": item_bills, "expenses": expenses, "sales": sales,
        "membershipSales": sales,  # web app is naam se padhta hai — alias
        "logs": logs, "tournaments": tournaments, "stats": stats,
        "serverNow": now_iso(),
    }
