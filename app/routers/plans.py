"""Club membership plans (wallet / frame pass / monthly) + sell-plan flow."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..mail import send_and_record, tpl_plan_sold
from ..models import PlanIn, PlanPatchIn, SellPlanIn
from ..services import billing_gate, get_club, grant_plan, public_member
from ..util import fmt, money, now_iso, uid

router = APIRouter(prefix="/clubs/{club_id}/plans", tags=["plans"])


@router.get("")
async def list_plans(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    plans = await db.plans.find({"clubId": club["id"]}).to_list(None)
    plans.sort(key=lambda p: (p.get("type", ""), p.get("amount", 0)))
    return plans


@router.post("", status_code=201)
async def create_plan(club_id: str, payload: PlanIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    if payload.type == "pass" and not payload.frames:
        raise HTTPException(400, "Frame pass needs the number of frames")
    if payload.type == "monthly" and not payload.days:
        raise HTTPException(400, "Monthly plan needs validity days")
    plan = {
        "id": uid("plan"), "clubId": club["id"], "name": payload.name.strip(),
        "type": payload.type, "amount": money(payload.amount),
        "value": money(payload.value or 0), "frames": payload.frames or 0,
        "days": payload.days or 0,
        "tableDiscountPercent": payload.tableDiscountPercent,
        "isDefault": payload.isDefault, "active": True,
        "description": (payload.description or "").strip(), "createdAt": now_iso(),
    }
    await db.plans.insert_one(plan)
    plan.pop("_id", None)
    return plan


@router.patch("/{plan_id}")
async def patch_plan(club_id: str, plan_id: str, payload: PlanPatchIn,
                     user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    ops = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "amount" in ops:
        ops["amount"] = money(ops["amount"])
    if "value" in ops:
        ops["value"] = money(ops["value"])
    if not ops:
        raise HTTPException(400, "Nothing to update")
    plan = await db.plans.find_one_and_update({"id": plan_id, "clubId": club["id"]},
                                              {"$set": ops})
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


@router.post("/{plan_id}/toggle-active")
async def toggle_plan(club_id: str, plan_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    plan = await db.plans.find_one({"id": plan_id, "clubId": club["id"]})
    if not plan:
        raise HTTPException(404, "Plan not found")
    return await db.plans.find_one_and_update({"id": plan_id},
                                              {"$set": {"active": not plan.get("active", True)}})


@router.delete("/{plan_id}")
async def delete_plan(club_id: str, plan_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    active_holders = await db.members.count_documents(
        {"clubId": club["id"], "planId": plan_id})
    if active_holders:
        raise HTTPException(400, f"{active_holders} player(s) hold this plan — can't delete it")
    res = await db.plans.delete_one({"id": plan_id, "clubId": club["id"]})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(404, "Plan not found")
    return {"ok": True, "message": "Plan deleted"}


@router.post("/{plan_id}/sell")
async def sell_plan(club_id: str, plan_id: str, payload: SellPlanIn,
                    user: dict = Depends(current_user)):
    """Sell a plan to a member — never double-sell, credits wallet/frames/expiry,
    writes a MembershipSale + PAYMENT log, mails the member when possible."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    plan = await db.plans.find_one({"id": plan_id, "clubId": club["id"]})
    if not plan or not plan.get("active", True):
        raise HTTPException(404, "Plan not found (or inactive)")
    member = await db.members.find_one({"id": payload.memberId, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    if member.get("planId") == plan_id and member.get("planType") == plan["type"]:
        raise HTTPException(400, f"{member['name']} already has {plan['name']} active")

    member, sale, effect = await grant_plan(
        club, member, plan, payload.mode, actor=user.get("name", ""), origin="sell")
    if member.get("email"):
        subj, html = tpl_plan_sold(club.get("name", "Rowdy's Den"), member, plan)
        await send_and_record("plan_sale", member["email"], subj, html,
                              club_id=club["id"], member_id=member["id"])
    return {"member": public_member(member), "sale": sale,
            "message": f"Plan sold · {plan['name']} → {member['name']} · ₹{fmt(plan['amount'])}"}
