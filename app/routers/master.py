"""Master Admin — platform overview, user/subscription management, plan catalog CRUD,
mailout records, per-club subscription patch."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import master_user
from ..mail import send_and_record, tpl_subscription
from ..models import (MasterPlanIn, MasterPlanPatchIn, MasterSubPatchIn,
                      MasterUserPatchIn)
from ..util import iso, money, now_iso, now_utc, parse_iso, uid

router = APIRouter(prefix="/master", tags=["master"])


@router.get("/overview")
async def overview(user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    users = await db.users.find({}).to_list(None)
    clubs = await db.clubs.find({}).to_list(None)
    plans = await db.subscription_plans.find({}).to_list(None)
    active = [u for u in users if (u.get("subscription") or {}).get("status")
              in ("trial", "active")]
    mrr = 0.0
    for u in users:
        sub = u.get("subscription") or {}
        if sub.get("status") in ("trial", "active") and sub.get("price"):
            monthly = sub["price"] / 12 if sub.get("billingCycle") == "yearly" else sub["price"]
            mrr += monthly
    club_name = {c["id"]: c["name"] for c in clubs}
    owner_name = {u["id"]: u.get("name", "") for u in users}
    non_master = [u for u in users if u.get("role") != "master"]
    for u in users:
        u["clubNames"] = [club_name.get(cid, cid) for cid in (u.get("clubIds") or [])]
    clubs_out = [{
        "id": c["id"], "name": c["name"], "logo": c.get("logo", ""),
        "location": c.get("location", ""), "ownerUserId": c.get("ownerUserId"),
        "ownerName": owner_name.get(c.get("ownerUserId"), ""),
        "createdAt": c.get("createdAt", ""),
    } for c in clubs]
    return {
        "accounts": len(non_master),
        "totalClubs": len(clubs), "activeSubs": len(active),
        "mrr": money(mrr), "sellerPlans": len(plans),
        # ---- web MasterAdmin aliases ----
        "stats": {
            "totalUsers": len(non_master),
            "activeUsers": len([u for u in non_master if u.get("active", True)]),
            "totalClubs": len(clubs),
            "activeSubscriptions": len(active),
            "monthlyRecurringRevenue": money(mrr),
        },
        "users": users,
        "clubs": clubs_out,
    }


@router.get("/users")
async def list_users(q: str = "", user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    users = await db.users.find({}).to_list(None)
    clubs = await db.clubs.find({}).to_list(None)
    club_name = {c["id"]: c["name"] for c in clubs}
    q = q.strip().lower()
    out = []
    for u in users:
        u["clubNames"] = [club_name.get(cid, cid) for cid in (u.get("clubIds") or [])]
        if q:
            hay = " ".join([u.get("name", ""), u.get("email", ""), u.get("id", ""),
                            " ".join(u.get("clubIds") or []),
                            " ".join(u["clubNames"])]).lower()
            if q not in hay:
                continue
        out.append(u)
    out.sort(key=lambda u: u.get("createdAt", ""))
    return out


@router.get("/mailouts")
async def mailouts(user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    mails = await db.mailouts.find({}, sort=[("createdAt", -1)]).to_list(100)
    for m in mails:
        m.pop("html", None)  # strip heavy bodies from the listing
    return mails


# ----------------------------------------------------------- plan catalog
@router.post("/subscription-plans", status_code=201)
async def create_plan(payload: MasterPlanIn, user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    plan = {"id": uid("sp"), **payload.model_dump(), "active": True,
            "createdAt": now_iso()}
    plan["price"] = money(plan["price"])
    await db.subscription_plans.insert_one(plan)
    plan.pop("_id", None)
    return plan


@router.get("/subscription-plans")
async def list_plans_admin(user: dict = Depends(master_user)):
    """Full catalog (incl. inactive) — Master Admin plan management."""
    db = await db_mod.get_db()
    plans = await db.subscription_plans.find({}).to_list(None)
    plans.sort(key=lambda p: (p.get("sortOrder", 0), p.get("price", 0)))
    for p in plans:
        p.pop("_id", None)
    return plans


@router.patch("/subscription-plans/{plan_id}")
async def patch_plan(plan_id: str, payload: MasterPlanPatchIn,
                     user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    ops = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "price" in ops:
        ops["price"] = money(ops["price"])
    if not ops:
        raise HTTPException(400, "Nothing to update")
    plan = await db.subscription_plans.find_one_and_update({"id": plan_id}, {"$set": ops})
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


@router.post("/subscription-plans/{plan_id}/toggle-active")
async def toggle_plan(plan_id: str, user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    plan = await db.subscription_plans.find_one({"id": plan_id})
    if not plan:
        raise HTTPException(404, "Plan not found")
    return await db.subscription_plans.find_one_and_update(
        {"id": plan_id}, {"$set": {"active": not plan.get("active", True)}})


@router.delete("/subscription-plans/{plan_id}")
async def delete_plan(plan_id: str, user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    holders = await db.users.count_documents({"subscription.planId": plan_id})
    if holders:
        raise HTTPException(400, f"{holders} account(s) are on this plan — deactivate it instead")
    res = await db.subscription_plans.delete_one({"id": plan_id})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(404, "Plan not found")
    return {"ok": True, "message": "Plan deleted"}


# ------------------------------------------------------------- users & subs
@router.patch("/users/{user_id}")
async def patch_user(user_id: str, payload: MasterUserPatchIn,
                     user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if target.get("role") == "master" and payload.role:
        raise HTTPException(400, "Master Admin role can't be changed here")
    ops = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not ops:
        raise HTTPException(400, "Nothing to update")
    ops["updatedAt"] = now_iso()
    return await db.users.find_one_and_update({"id": user_id}, {"$set": ops})


@router.patch("/users/{user_id}/subscription")
async def patch_subscription(user_id: str, payload: MasterSubPatchIn,
                             user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    sub = dict(target.get("subscription") or {})
    before = sub.get("status")
    if payload.planId:
        plan = await db.subscription_plans.find_one({"id": payload.planId})
        if not plan:
            raise HTTPException(404, "Plan not found")
        sub.update({"planId": plan["id"], "planName": plan["name"],
                    "price": plan["price"], "billingCycle": plan.get("billingCycle", "monthly"),
                    "durationDays": plan.get("durationDays", 30),
                    "maxClubs": plan.get("maxClubs", 1)})
    for key in ("planName", "status", "price", "durationDays", "maxClubs",
                "startsAt", "expiresAt", "notes"):
        val = getattr(payload, key)
        if val is not None:
            sub[key] = val
    if payload.status in ("trial", "active"):
        now = now_utc()
        sub.setdefault("startsAt", iso(now))
        if payload.expiresAt is None and not sub.get("expiresAt"):
            days = sub.get("durationDays") or (plan.get("trialDays") if payload.status == "trial"
                                               and payload.planId else 30)
            sub["expiresAt"] = iso(now + timedelta(days=int(days or 30)))
    sub["updatedAt"] = now_iso()
    target = await db.users.find_one_and_update({"id": user_id},
                                                {"$set": {"subscription": sub,
                                                          "updatedAt": now_iso()}})
    if payload.status and payload.status != before and target.get("email"):
        subj, html = tpl_subscription(target.get("name", ""), sub.get("planName", "Plan"),
                                      payload.status, sub.get("expiresAt") or "",
                                      sub.get("price") or 0)
        await send_and_record("subscription", target["email"], subj, html, user_id=user_id)
    return target


@router.delete("/users/{user_id}/subscription")
async def delete_subscription(user_id: str, user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    target = await db.users.find_one_and_update(
        {"id": user_id}, {"$set": {"subscription": None, "updatedAt": now_iso()}})
    return target


@router.patch("/clubs/{club_id}/subscription")
async def patch_club_subscription(club_id: str, payload: MasterSubPatchIn,
                                  user: dict = Depends(master_user)):
    db = await db_mod.get_db()
    club = await db.clubs.find_one({"id": club_id})
    if not club:
        raise HTTPException(404, "Club not found")
    return await patch_subscription(club["ownerUserId"], payload, user)
