"""Public subscription-plan catalog + account subscription select/status."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..mail import send_and_record, tpl_subscription
from ..models import SelectPlanIn
from ..util import iso, now_utc, parse_iso

router = APIRouter(tags=["subscriptions"])


@router.get("/subscription-plans")
async def public_plans():
    db = await db_mod.get_db()
    plans = await db.subscription_plans.find({"active": True}).to_list(None)
    plans.sort(key=lambda p: (p.get("sortOrder", 0), p.get("price", 0)))
    for p in plans:
        p.pop("_id", None)
    return plans


@router.get("/account/subscription")
async def my_subscription(user: dict = Depends(current_user)):
    sub = user.get("subscription")
    return {"subscription": sub}


@router.post("/account/subscription/select")
async def select_plan(payload: SelectPlanIn, user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    if user["id"] == "local-admin":
        raise HTTPException(400, "Auth is disabled — no subscription needed")
    plan = await db.subscription_plans.find_one({"id": payload.planId, "active": True})
    if not plan:
        raise HTTPException(404, "That plan isn't available any more")
    existing = user.get("subscription") or {}
    if existing.get("status") in ("active", "trial"):
        if not existing.get("expiresAt"):
            raise HTTPException(400, "Your subscription is active. Contact support to change plans.")
        try:
            if now_utc() < parse_iso(existing["expiresAt"]):
                raise HTTPException(400, "Your subscription is active. Contact support to change plans.")
        except ValueError as exc:
            raise HTTPException(400, "Your subscription has an invalid expiry. Contact support.") from exc
    now = now_utc()
    trial = plan.get("trialDays", 0) > 0 and not user.get("trialUsedAt") and not existing.get("trialUsedAt")
    if trial:
        claim = await db.users.update_one(
            {"id": user["id"], "trialUsedAt": {"$exists": False}},
            {"$set": {"trialUsedAt": iso(now)}})
        trial = bool(getattr(claim, "modified_count", 0))
    status = "trial" if trial else "pending"
    sub = {
        "planId": plan["id"], "planName": plan["name"], "status": status,
        "price": plan.get("price", 0), "billingCycle": plan.get("billingCycle", "monthly"),
        "durationDays": plan.get("durationDays", 30), "maxClubs": plan.get("maxClubs", 1),
        "selectedAt": iso(now),
        "notes": "", "updatedAt": iso(now),
    }
    if trial:
        sub["startsAt"] = iso(now)
        sub["expiresAt"] = iso(now + timedelta(days=int(plan.get("trialDays", 0))))
        sub["trialUsedAt"] = iso(now)
    await db.users.update_one({"id": user["id"]},
                              {"$set": {"subscription": sub, "updatedAt": iso(now)}})
    user["subscription"] = sub
    if trial:
        user["trialUsedAt"] = iso(now)
    subj, html = tpl_subscription(user.get("name", ""), plan["name"], status,
                                  sub.get("expiresAt") or "", plan.get("price", 0))
    await send_and_record("subscription", user["email"], subj, html, user_id=user["id"])
    return {"user": user, "subscription": sub}
