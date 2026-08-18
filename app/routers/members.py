"""Members + wallet/due payments (old-due-first ledger rule) + notify mail."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..mail import send_and_record, tpl_balance_notify
from ..models import (MemberIn, MemberPatchIn, MemberPaymentIn, NotifyIn,
                      PlanPaymentIn)
from ..services import (billing_gate, book_plan_sale, get_club, grant_plan,
                        payment_log, public_member, write_log)
from ..util import fmt, money, now_iso, parse_iso, uid

router = APIRouter(prefix="/clubs/{club_id}/members", tags=["members"])


@router.get("")
async def list_members(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    members = [public_member(m) for m in await db.members.find({"clubId": club["id"]}).to_list(None)]
    members.sort(key=lambda m: m.get("name", "").lower())
    return members


@router.post("", status_code=201)
async def create_member(club_id: str, payload: MemberIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    member = {
        "id": uid("m"), "clubId": club["id"], "name": payload.name.strip(),
        "phone": (payload.phone or "").strip(), "email": (payload.email or "").strip(),
        "type": "regular",
        "walletBalance": money(payload.walletBalance),  # opening credit (migrated)
        "dueAmount": money(payload.dueAmount),          # opening due (migrated)
        "passFramesLeft": int(payload.passFramesLeft or 0),  # opening frames (web)
        "planId": None, "planName": None, "planType": None,
        "planExpiresAt": None, "tableDiscountPercent": None,
        "active": True, "notes": (payload.notes or "").strip(),
        "createdAt": now_iso(), "updatedAt": now_iso(),
    }
    plan = None
    if payload.planId:  # web Add Player — plan select karte hi benefits + SALE book
        plan = await db.plans.find_one({"id": payload.planId, "clubId": club["id"]})
        if not plan or not plan.get("active", True):
            raise HTTPException(404, "Plan not found (or inactive)")
    await db.members.insert_one(member)
    if plan:
        # default planPaid=True — membership ka paisa turant ledger me (day-close /
        # monthly / finance sync). planPaid False → sirf benefits, paisa baad me
        # POST /{id}/plan-payment se book hoga.
        member, sale, effect = await grant_plan(
            club, member, plan, payload.planPaymentMode or payload.mode,
            actor=user.get("name", ""),
            book_payment=payload.planPaid, origin="join")
        await write_log(club["id"], "ADMIN",
                        f"Plan on join · {member['name']} · {plan['name']} · {effect}" +
                        ("" if sale else " · unpaid"),
                        actor=user.get("name", ""))
    member.pop("_id", None)
    return public_member(member)


@router.patch("/{member_id}")
async def patch_member(club_id: str, member_id: str, payload: MemberPatchIn,
                       user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    ops, unset = {}, {}
    for key in ("name", "phone", "email", "notes", "active"):
        val = getattr(payload, key)
        if val is not None:
            ops[key] = val.strip() if isinstance(val, str) else val
    for key in ("walletBalance", "dueAmount", "passFramesLeft"):  # direct-set (web)
        val = getattr(payload, key)
        if val is not None:
            ops[key] = int(val) if key == "passFramesLeft" else money(val)
    if payload.planExpiresAt is not None:
        raw = payload.planExpiresAt.strip()
        try:
            exp = parse_iso(raw if "T" in raw else raw + "T23:59:59+00:00")
        except ValueError:
            raise HTTPException(400, "That expiry date doesn't look right (use YYYY-MM-DD)")
        from ..util import iso
        ops["planExpiresAt"] = iso(exp)
        unset["expiryMailSentAt"] = ""  # re-arm the sweep on the new date
    plan = None
    if payload.planId:  # Edit se DIFFERENT plan assign = abhi sell + book
        plan = await db.plans.find_one({"id": payload.planId, "clubId": club["id"]})
        if not plan or not plan.get("active", True):
            raise HTTPException(404, "Plan not found (or inactive)")
        if member.get("planId") == plan["id"]:
            plan = None  # same plan ka echo — double charge kabhi nahi
    if not ops and not unset and plan is None:
        raise HTTPException(400, "Nothing to update")
    if plan:
        member, _sale, _effect = await grant_plan(
            club, member, plan, payload.planPaymentMode or payload.mode or "cash",
            actor=user.get("name", ""), origin="edit")
    if ops or unset:
        ops["updatedAt"] = now_iso()
        update = {"$set": ops}
        if unset:
            update["$unset"] = unset
        member = await db.members.find_one_and_update({"id": member_id}, update)
    return public_member(member)


@router.delete("/{member_id}")
async def delete_member(club_id: str, member_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    if (member.get("dueAmount") or 0) > 0:
        raise HTTPException(400, f"Clear {member['name']}'s pending due (₹{fmt(member['dueAmount'])}) first")
    await db.members.delete_one({"id": member_id})
    return {"ok": True, "message": f"Player deleted · {member['name']}"}


@router.post("/{member_id}/payments")
async def member_payment(club_id: str, member_id: str, payload: MemberPaymentIn,
                         user: dict = Depends(current_user)):
    """Due Desk collect — part/full, OLD-DUE-FIRST, then PAYMENT log."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    due = money(member.get("dueAmount") or 0)
    if due <= 0:
        raise HTTPException(400, f"{member['name']} has no pending due")
    if payload.amount > due:
        raise HTTPException(400, f"Payment exceeds the pending due (₹{fmt(due)})")
    amount = money(payload.amount)
    member = await db.members.find_one_and_update(
        {"id": member_id}, {"$inc": {"dueAmount": -amount},
                            "$set": {"updatedAt": now_iso()}})
    left = money(member.get("dueAmount") or 0)
    await payment_log(club["id"], "due", amount, payload.mode,
                      f"Due collected · {member['name']} · ₹{fmt(amount)}" +
                      ("" if left == 0 else f" · ₹{fmt(left)} left"),
                      actor=user.get("name", ""), member_id=member_id)
    return {"member": public_member(member),
            "message": f"Due collected · {member['name']} ₹{fmt(amount)}" +
                       ("" if left == 0 else f" · ₹{fmt(left)} left")}


@router.post("/{member_id}/plan-payment")
async def member_plan_payment(club_id: str, member_id: str, payload: PlanPaymentIn,
                              user: dict = Depends(current_user)):
    """Plan assign to ho gaya tha par paisa book nahi hua (planPaid=false ya
    purana data) — ab paisa aaya to yahan book karo. Benefits DOBARA nahi milte
    (sirf paisa). Idempotent: same (player, plan) pe sirf ek baar."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    if not member.get("planId"):
        raise HTTPException(400, f"{member['name']} has no plan assigned — nothing to book")
    plan = await db.plans.find_one({"id": member["planId"], "clubId": club["id"]})
    if not plan:
        raise HTTPException(400, f"{member['name']}'s plan record is missing — can't book it")
    dup = await db.membership_sales.find_one(
        {"clubId": club["id"], "memberId": member_id, "planId": plan["id"]})
    if dup:
        raise HTTPException(400, f"Payment already booked · {member['name']} · {plan['name']}")
    sale = await book_plan_sale(club, member, plan, payload.mode,
                                actor=user.get("name", ""), origin="reconcile")
    return {"member": public_member(member), "sale": sale,
            "message": f"Plan payment booked · {plan['name']} → {member['name']} · "
                       f"₹{fmt(plan['amount'])}"}


@router.post("/{member_id}/notify")
async def notify_member(club_id: str, member_id: str, payload: NotifyIn,
                        user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Player not found")
    if not member.get("email"):
        raise HTTPException(400, "Add the email id on the player card first")
    subj, html = tpl_balance_notify(club.get("name", "Rowdy's Den"), member)
    mail = await send_and_record("balance_notify", member["email"], subj, html,
                                 club_id=club["id"], member_id=member_id)
    msg = f"Account summary mailed to {member['name']}" if mail.get("sent") \
        else f"Mail recorded for {member['name']} (SMTP not configured)"
    return {"ok": True, "message": msg, "sent": mail.get("sent", False)}
