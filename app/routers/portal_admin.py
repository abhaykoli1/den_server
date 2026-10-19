"""Owner/staff side of the members app — `/api/clubs/{club_id}/portal/*`.

Members raise requests from the app that only the counter can confirm:
  · wallet top-ups paid on the club UPI QR (UTR reference)
  · membership plans paid on the club UPI QR (UTR reference)
  · app orders (kitchen fulfilment: placed → preparing → ready → served)

Approving a top-up / plan books the sale in the PAYMENT ledger exactly like a
counter sale, so day-close and monthly reports stay correct.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db as db_mod
from ..auth import current_user
from ..models import In
from ..portal import bill_view
from ..services import billing_gate, get_club, grant_plan, payment_log, public_member, write_log
from ..util import fmt, money, now_iso

router = APIRouter(prefix="/clubs/{club_id}/portal", tags=["members-app admin"])


class ApproveIn(In):
    mode: Literal["upi", "cash", "card"] = "upi"
    note: Optional[str] = Field(default=None, max_length=200)


class RejectIn(In):
    reason: str = Field(default="", max_length=200)


class FulfilIn(In):
    status: Literal["placed", "preparing", "ready", "served", "cancelled"]


def _clean(doc):
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


# ------------------------------------------------------------- top-ups
@router.get("/topup-requests")
async def list_topups(club_id: str, status: str = "pending", user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    q = {"clubId": club["id"]}
    if status and status != "all":
        q["status"] = status
    rows = [_clean(r) for r in await db.wallet_topup_requests.find(q).to_list(500)]
    rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    return {"requests": rows}


@router.post("/topup-requests/{req_id}/approve")
async def approve_topup(club_id: str, req_id: str, payload: ApproveIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    req = await db.wallet_topup_requests.find_one({"id": req_id, "clubId": club["id"]})
    if not req:
        raise HTTPException(404, "Request not found")
    if req.get("status") != "pending":
        raise HTTPException(400, f"Request already {req.get('status')}")
    member = await db.members.find_one({"id": req["memberId"], "clubId": club["id"]})
    if not member:
        raise HTTPException(404, "Member no longer exists")
    amount = money(req["amount"])
    now = now_iso()
    member = await db.members.find_one_and_update(
        {"id": member["id"]}, {"$inc": {"walletBalance": amount}, "$set": {"updatedAt": now}})
    req = await db.wallet_topup_requests.find_one_and_update(
        {"id": req_id}, {"$set": {"status": "approved", "approvedAt": now, "approvedBy": user.get("name", ""),
                                  "mode": payload.mode, "note": payload.note or ""}})
    await payment_log(club["id"], "memberships", amount, payload.mode,
                      f"Wallet top-up · {member['name']} · ₹{fmt(amount)} · UPI ref {req.get('utr', '')}",
                      actor=user.get("name", ""), member_id=member["id"], ref_type="topup_request", ref_id=req_id)
    return {"request": _clean(req), "member": public_member(member),
            "message": f"₹{fmt(amount)} added to {member['name']}'s wallet"}


@router.post("/topup-requests/{req_id}/reject")
async def reject_topup(club_id: str, req_id: str, payload: RejectIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    req = await db.wallet_topup_requests.find_one({"id": req_id, "clubId": club["id"]})
    if not req:
        raise HTTPException(404, "Request not found")
    if req.get("status") != "pending":
        raise HTTPException(400, f"Request already {req.get('status')}")
    req = await db.wallet_topup_requests.find_one_and_update(
        {"id": req_id}, {"$set": {"status": "rejected", "rejectedAt": now_iso(), "rejectedBy": user.get("name", ""),
                                  "reason": payload.reason}})
    await write_log(club["id"], "ADMIN", f"Top-up request rejected · {req.get('memberName')} · ₹{fmt(req.get('amount') or 0)}"
                    + (f" · {payload.reason}" if payload.reason else ""),
                    actor=user.get("name", ""), member_id=req.get("memberId"))
    return {"request": _clean(req), "message": "Request rejected"}


# --------------------------------------------------- membership requests
@router.get("/membership-requests")
async def list_membership_requests(club_id: str, status: str = "pending", user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    q = {"clubId": club["id"]}
    if status and status != "all":
        q["status"] = status
    rows = [_clean(r) for r in await db.membership_requests.find(q).to_list(500)]
    rows.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    return {"requests": rows}


@router.post("/membership-requests/{req_id}/approve")
async def approve_membership(club_id: str, req_id: str, payload: ApproveIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    req = await db.membership_requests.find_one({"id": req_id, "clubId": club["id"]})
    if not req:
        raise HTTPException(404, "Request not found")
    if req.get("status") != "pending":
        raise HTTPException(400, f"Request already {req.get('status')}")
    member = await db.members.find_one({"id": req["memberId"], "clubId": club["id"]})
    plan = await db.plans.find_one({"id": req["planId"], "clubId": club["id"]})
    if not member or not plan:
        raise HTTPException(404, "Member or plan no longer exists")
    member, sale, effect = await grant_plan(club, member, plan, mode=payload.mode,
                                            actor=user.get("name", ""), origin="member_request")
    req = await db.membership_requests.find_one_and_update(
        {"id": req_id}, {"$set": {"status": "approved", "approvedAt": now_iso(), "approvedBy": user.get("name", ""),
                                  "saleId": sale["id"] if sale else None, "note": payload.note or ""}})
    return {"request": _clean(req), "member": public_member(member), "sale": sale,
            "message": f"{plan['name']} activated for {member['name']} · {effect}"}


@router.post("/membership-requests/{req_id}/reject")
async def reject_membership(club_id: str, req_id: str, payload: RejectIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    req = await db.membership_requests.find_one({"id": req_id, "clubId": club["id"]})
    if not req:
        raise HTTPException(404, "Request not found")
    if req.get("status") != "pending":
        raise HTTPException(400, f"Request already {req.get('status')}")
    req = await db.membership_requests.find_one_and_update(
        {"id": req_id}, {"$set": {"status": "rejected", "rejectedAt": now_iso(), "rejectedBy": user.get("name", ""),
                                  "reason": payload.reason}})
    await write_log(club["id"], "ADMIN", f"Membership request rejected · {req.get('memberName')} · {req.get('planName')}"
                    + (f" · {payload.reason}" if payload.reason else ""),
                    actor=user.get("name", ""), member_id=req.get("memberId"))
    return {"request": _clean(req), "message": "Request rejected"}


# ------------------------------------------------------------ app orders
@router.get("/orders")
async def list_app_orders(club_id: str, status: str = "open", user: dict = Depends(current_user)):
    """Kitchen / counter view of app orders. open = not yet served."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    rows = await db.item_bills.find({"clubId": club["id"], "source": "member_order"},
                                    sort=[("createdAt", -1)]).to_list(300)
    views = [bill_view(b) for b in rows]
    if status == "open":
        views = [v for v in views if v["fulfilment"] not in ("served", "cancelled")]
    return {"orders": views}


@router.post("/orders/{order_id}/fulfilment")
async def set_fulfilment(club_id: str, order_id: str, payload: FulfilIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    bill = await db.item_bills.find_one({"id": order_id, "clubId": club["id"], "source": "member_order"})
    if not bill:
        raise HTTPException(404, "Order not found")
    if payload.status == "cancelled":
        raise HTTPException(400, "Cancel by deleting the bill (stock + wallet/due are restored) — DELETE /item-bills/{id}")
    bill = await db.item_bills.find_one_and_update(
        {"id": order_id}, {"$set": {"fulfilment": payload.status, "updatedAt": now_iso(),
                                    f"fulfilment_{payload.status}At": now_iso()}})
    await write_log(club["id"], "BILLING", f"App order {payload.status} · {bill.get('customerName')} · {bill.get('deliverTo', '')}",
                    actor=user.get("name", ""), member_id=bill.get("memberId"), ref_type="item_bill", ref_id=order_id)
    return {"order": bill_view(bill), "message": f"Order marked {payload.status}"}
