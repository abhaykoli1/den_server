"""Member-only API — `/api/member-portal/*`.

Everything a signed-in club member can read or do from the members app:
home feed, catalogue (cafe + pro-shop), cart checkout, orders, wallet + dues,
frames, rankings, bookings, tournaments, membership plans, activity, profile.

Money rules (mirrors the counter so reports never double count):
  · wallet payments are logged with `mode: wallet` (consumption of prepaid money)
  · "pay at counter" / UPI-pending orders are `due` bills — the member's
    `dueAmount` rises until staff mark the bill paid (existing mark-paid flow)
  · every due increase respects the club's `dueLimit`
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db as db_mod
from ..auth import current_user
from ..models import In
from ..portal import (catalog, club_tournaments, due_breakdown, frame_stats,
                      home_payload, live_tables, member_context, member_summary,
                      membership_status, my_activity, my_bills, my_bookings,
                      my_frames, my_requests, plan_view, public_club, public_item,
                      sold_counts, wallet_ledger, bill_view)
from ..services import grant_plan, payment_log, write_log
from ..util import IST, fmt, iso, money, now_iso, now_utc, uid
from .bookings import (BookingIn, CLOSE_HOUR, CLOSE_MIN, MAX_DAYS_AHEAD, OPEN_HOUR,
                       cancel_booking as _cancel_booking,
                       create_booking as _create_booking, _sweep)

router = APIRouter(prefix="/member-portal", tags=["member-portal"])

UTR_RE = re.compile(r"^[A-Za-z0-9\-_/ ]{4,40}$")


# ------------------------------------------------------------------- models
class OrderLineIn(In):
    menuItemId: str
    quantity: int = Field(ge=1, le=50)


class OrderIn(In):
    items: list[OrderLineIn] = Field(min_length=1, max_length=40)
    mode: str = "wallet"                # wallet | counter | upi
    utr: Optional[str] = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=200)
    tableId: Optional[str] = None       # bring it to my table


class PayDueIn(In):
    amount: Optional[float] = Field(default=None, gt=0, le=1000000)  # None = full


class TopupIn(In):
    amount: float = Field(gt=0, le=100000)
    utr: str = Field(min_length=4, max_length=40)


class MembershipRequestIn(In):
    planId: str
    utr: str = Field(min_length=4, max_length=40)


class BuyPlanIn(In):
    mode: str = "wallet"


class JoinIn(In):
    mode: str = "counter"               # counter | wallet


class ProfileIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)


class SwitchClubIn(In):
    clubId: str


class SeenIn(In):
    at: Optional[str] = None


# ------------------------------------------------------------------ helpers
def _clean(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _due_limit(club: dict) -> float:
    return money((club.get("settings") or {}).get("dueLimit") or 0)


def _check_due_room(club: dict, member: dict, add: float, what: str = "this order"):
    limit = _due_limit(club)
    current = money(member.get("dueAmount") or 0)
    if limit and money(current + add) > limit:
        room = money(max(0.0, limit - current))
        raise HTTPException(
            400, f"Your tab limit is ₹{fmt(limit)} (₹{fmt(current)} pending) — "
                 f"₹{fmt(room)} room left. Pay from wallet or clear dues at the counter for {what}.")


def _valid_utr(utr: str) -> str:
    utr = (utr or "").strip()
    if not UTR_RE.match(utr):
        raise HTTPException(400, "Enter the UPI reference / UTR number from your payment app (4–40 characters)")
    return utr


async def _user_doc(db, user: dict) -> dict:
    return await db.users.find_one({"id": user.get("id")}) or user


# --------------------------------------------------------------------- home
@router.get("/home")
async def home(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    udoc = await _user_doc(db, user)
    return await home_payload(db, member, club, udoc)


@router.get("/dashboard")
async def dashboard(user: dict = Depends(current_user)):
    """Back-compat shape for older clients + the live-tables poller."""
    db, member, club = await member_context(user)
    tables = await live_tables(db, club, member["id"])
    frames = await my_frames(db, club, member["id"])
    bills = await my_bills(db, club, member["id"])
    cat = await catalog(db, club["id"], include_out_of_stock=False)
    udoc = await _user_doc(db, user)
    act = await my_activity(db, club, member, udoc.get("activitySeenAt"), limit=50)
    return {
        "member": member_summary(member), "club": public_club(club), "tables": tables,
        "dueFrames": [f for f in frames if f["myDue"] > 0], "dues": due_breakdown(member, frames, bills),
        "menuItems": cat["items"], "activity": act["items"], "orders": bills,
        "serverNow": now_iso(),
    }


@router.get("/tables")
async def tables(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    return {"tables": await live_tables(db, club, member["id"]), "serverNow": now_iso(),
            "isOpen": public_club(club)["isOpen"]}


# ---------------------------------------------------------------- catalogue
@router.get("/catalog")
async def get_catalog(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    cat = await catalog(db, club["id"])
    cat["club"] = public_club(club)
    cat["walletBalance"] = money(member.get("walletBalance") or 0)
    return cat


@router.get("/items/{item_id}")
async def get_item(item_id: str, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    item = await db.menu_items.find_one({"id": item_id, "clubId": club["id"], "active": {"$ne": False}})
    if not item:
        raise HTTPException(404, "Item not found")
    sold = await sold_counts(db, club["id"])
    view = public_item(item, sold)
    cat = await catalog(db, club["id"], include_out_of_stock=False)
    related = [v for v in cat["items"] if v["id"] != item_id and v["category"] == view["category"]][:6]
    if len(related) < 4:
        related += [v for v in cat["items"] if v["id"] != item_id and v["kind"] == view["kind"]
                    and v not in related][:6 - len(related)]
    return {"item": view, "related": related}


# ------------------------------------------------------------------- orders
@router.get("/orders")
async def orders(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    bills = await my_bills(db, club, member["id"])
    return {"orders": bills, "dueAmount": money(member.get("dueAmount") or 0),
            "walletBalance": money(member.get("walletBalance") or 0)}


@router.get("/orders/{order_id}")
async def order_detail(order_id: str, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    bill = await db.item_bills.find_one({"id": order_id, "clubId": club["id"], "memberId": member["id"]})
    if not bill:
        raise HTTPException(404, "Order not found")
    return {"order": bill_view(bill), "walletBalance": money(member.get("walletBalance") or 0)}


async def _restore(db, lines: list[dict]):
    for line in lines:
        await db.menu_items.update_one({"id": line["menuItemId"]}, {"$inc": {"stockQty": line["qty"]}})


@router.post("/orders", status_code=201)
async def create_order(payload: OrderIn, user: dict = Depends(current_user)):
    """Cart checkout. wallet = paid now · counter = due bill (pay when you leave) ·
    upi = paid outside via the club QR, bill stays due until staff confirm the UTR."""
    mode = (payload.mode or "wallet").strip().lower()
    if mode not in ("wallet", "counter", "upi"):
        raise HTTPException(400, "Choose wallet, pay-at-counter or UPI")
    db, member, club = await member_context(user)
    if (club.get("settings") or {}).get("isOpen") is False:
        raise HTTPException(400, "The club is closed right now — orders open again when we do")
    utr = _valid_utr(payload.utr) if mode == "upi" else None
    if mode == "upi" and not club.get("qrCode") and not public_club(club)["upiId"]:
        raise HTTPException(400, "UPI payments aren't set up for this club yet — use wallet or pay at the counter")

    table_name = ""
    if payload.tableId:
        table = await db.tables.find_one({"id": payload.tableId, "clubId": club["id"]})
        if not table:
            raise HTTPException(404, "Table not found")
        table_name = table.get("name", "")

    # merge duplicate lines
    wanted: dict = {}
    for line in payload.items:
        wanted[line.menuItemId] = wanted.get(line.menuItemId, 0) + line.quantity
    lines, total, cost_total = [], 0.0, 0.0
    for item_id, qty in wanted.items():
        if qty > 50:
            await _restore(db, lines)
            raise HTTPException(400, "Max 50 of one item per order")
        item = await db.menu_items.find_one_and_update(
            {"id": item_id, "clubId": club["id"], "active": {"$ne": False}, "stockQty": {"$gte": qty}},
            {"$inc": {"stockQty": -qty}}, after=False)
        if not item:
            await _restore(db, lines)
            probe = await db.menu_items.find_one({"id": item_id, "clubId": club["id"]})
            if not probe or probe.get("active") is False:
                raise HTTPException(400, "One item in your cart is no longer available")
            raise HTTPException(400, f"Only {int(probe.get('stockQty') or 0)} × {probe.get('name')} left — reduce the quantity")
        price = money(item.get("price") or 0)
        cost = money(item.get("costPrice") or 0)
        amount = money(price * qty)
        total = money(total + amount)
        cost_total = money(cost_total + money(cost * qty))
        lines.append({"menuItemId": item["id"], "name": item["name"], "price": price, "qty": qty,
                      "amount": amount, "costPrice": cost, "costAmount": money(cost * qty)})

    wallet = money(member.get("walletBalance") or 0)
    if mode == "wallet":
        if wallet < total:
            await _restore(db, lines)
            raise HTTPException(400, f"Wallet is short — ₹{fmt(wallet)} available, order is ₹{fmt(total)}. "
                                     f"Top up, or choose pay at counter.")
        member = await db.members.find_one_and_update(
            {"id": member["id"]}, {"$inc": {"walletBalance": -total}, "$set": {"updatedAt": now_iso()}})
        payments = [{"mode": "wallet", "amount": total}]
    else:
        try:
            _check_due_room(club, member, total)
        except HTTPException:
            await _restore(db, lines)
            raise
        member = await db.members.find_one_and_update(
            {"id": member["id"]}, {"$inc": {"dueAmount": total}, "$set": {"updatedAt": now_iso()}})
        payments = []

    now = now_iso()
    deliver = f"Table · {table_name}" if table_name else "Counter pickup"
    bill = {
        "id": uid("order"), "clubId": club["id"], "items": lines,
        "customerName": member.get("name"), "memberId": member["id"], "memberName": member.get("name"),
        "source": "member_order", "channel": "members_app",
        "mode": "wallet" if mode == "wallet" else "due",
        "paymentMode": "wallet" if mode == "wallet" else "due",
        "requestedMode": mode, "payments": payments,
        "subtotal": total, "discount": 0.0, "amount": total, "total": total,
        "costAmount": cost_total, "profit": money(total - cost_total),
        "paidAmount": total if mode == "wallet" else 0.0,
        "dueAmount": 0.0 if mode == "wallet" else total,
        "paid": mode == "wallet", "status": "paid" if mode == "wallet" else "unpaid",
        "deliverTo": deliver, "tableId": payload.tableId, "note": payload.note.strip(),
        "fulfilment": "placed",
        "createdAt": now, "paidAt": now if mode == "wallet" else None, "updatedAt": now,
        "createdBy": member.get("name", ""),
    }
    if mode == "upi":
        bill["paymentRef"] = utr
        bill["verification"] = "pending"
    await db.item_bills.insert_one(bill)
    bill.pop("_id", None)

    parts = ", ".join(f"{li['name']}×{li['qty']}" for li in lines)
    if mode == "wallet":
        await payment_log(club["id"], "items", total, "wallet",
                          f"App order · {member.get('name')} · ₹{fmt(total)} (wallet) · {parts}",
                          actor=member.get("name", ""), member_id=member["id"],
                          ref_type="item_bill", ref_id=bill["id"])
    else:
        await write_log(club["id"], "WARNING",
                        f"App order on tab · {member.get('name')} · ₹{fmt(total)} · {parts}"
                        + (f" · UPI ref {utr} (verify)" if utr else " · pay at counter"),
                        actor=member.get("name", ""), member_id=member["id"],
                        ref_type="item_bill", ref_id=bill["id"], amount=total)
    await write_log(club["id"], "BILLING", f"New app order · {member.get('name')} · {deliver} · {parts}",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="item_bill", ref_id=bill["id"])
    msg = {"wallet": "Order placed and paid from your wallet",
           "counter": "Order placed — pay at the counter before you leave",
           "upi": "Order placed — we'll confirm your UPI payment shortly"}[mode]
    return {"order": bill_view(bill), "message": msg,
            "walletBalance": money(member.get("walletBalance") or 0),
            "dueAmount": money(member.get("dueAmount") or 0)}


@router.post("/orders/{order_id}/pay")
async def pay_order(order_id: str, user: dict = Depends(current_user)):
    """Settle ONE unpaid order from the wallet (mirrors counter mark-paid)."""
    db, member, club = await member_context(user)
    bill = await db.item_bills.find_one({"id": order_id, "clubId": club["id"], "memberId": member["id"]})
    if not bill:
        raise HTTPException(404, "Order not found")
    view = bill_view(bill)
    outstanding = view["dueAmount"]
    if view["paid"] or outstanding <= 0:
        raise HTTPException(400, "This order is already paid")
    wallet = money(member.get("walletBalance") or 0)
    if wallet < outstanding:
        raise HTTPException(400, f"Wallet is short — ₹{fmt(wallet)} available, ₹{fmt(outstanding)} needed")
    now = now_iso()
    payments = list(bill.get("payments") or []) + [{"mode": "wallet", "amount": outstanding}]
    bill = await db.item_bills.find_one_and_update(
        {"id": order_id},
        {"$set": {"paidAmount": money(view["paidAmount"] + outstanding), "dueAmount": 0.0, "paid": True,
                  "status": "paid", "paidAt": now, "updatedAt": now, "settledMode": "wallet",
                  "payments": payments, "verification": None}})
    member = await db.members.find_one_and_update(
        {"id": member["id"]},
        {"$inc": {"walletBalance": -outstanding, "dueAmount": -outstanding}, "$set": {"updatedAt": now}})
    await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}}, {"$set": {"dueAmount": 0}})
    await payment_log(club["id"], "items", outstanding, "wallet",
                      f"App order settled from wallet · {member.get('name')} · ₹{fmt(outstanding)}",
                      actor=member.get("name", ""), member_id=member["id"], ref_type="item_bill", ref_id=order_id)
    return {"order": bill_view(bill), "message": f"Paid ₹{fmt(outstanding)} from wallet",
            "walletBalance": money(member.get("walletBalance") or 0),
            "dueAmount": money(max(0.0, member.get("dueAmount") or 0))}


# ------------------------------------------------------------------- wallet
@router.get("/wallet")
async def wallet(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    frames = await my_frames(db, club, member["id"])
    bills = await my_bills(db, club, member["id"])
    plans = await db.plans.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    reqs = await my_requests(db, club, member["id"])
    return {
        "member": member_summary(member), "club": public_club(club),
        "dues": due_breakdown(member, frames, bills),
        "ledger": await wallet_ledger(db, club, member, frames, bills),
        "topupOffers": [plan_view(p, member) for p in plans if p.get("type") == "wallet"],
        "requests": reqs, "membership": membership_status(member),
    }


@router.post("/dues/pay")
async def pay_due(payload: PayDueIn, user: dict = Depends(current_user)):
    """Clear the tab from the wallet. Oldest unpaid bills are marked paid too so
    the counter's bill list agrees with the member's balance."""
    db, member, club = await member_context(user)
    due = money(member.get("dueAmount") or 0)
    wallet = money(member.get("walletBalance") or 0)
    if due <= 0:
        raise HTTPException(400, "You have no pending due — you're all clear")
    amount = money(payload.amount) if payload.amount else money(min(due, wallet))
    if amount > due:
        raise HTTPException(400, f"That's more than your due (₹{fmt(due)})")
    if amount > wallet:
        raise HTTPException(400, f"Wallet is short — ₹{fmt(wallet)} available")
    if amount <= 0:
        raise HTTPException(400, "Wallet balance is empty — top up first or pay at the counter")
    now = now_iso()
    member = await db.members.find_one_and_update(
        {"id": member["id"]},
        {"$inc": {"walletBalance": -amount, "dueAmount": -amount}, "$set": {"updatedAt": now}})
    await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}}, {"$set": {"dueAmount": 0}})
    # settle unpaid bills oldest-first with what was paid
    left = amount
    bills = await db.item_bills.find({"clubId": club["id"], "memberId": member["id"], "paid": {"$ne": True}}).to_list(200)
    bills.sort(key=lambda b: b.get("createdAt", ""))
    for b in bills:
        if left <= 0:
            break
        v = bill_view(b)
        if v["dueAmount"] <= 0:
            continue
        pay = money(min(left, v["dueAmount"]))
        new_due = money(v["dueAmount"] - pay)
        settled = new_due <= 0
        await db.item_bills.update_one({"id": b["id"]}, {"$set": {
            "paidAmount": money(v["paidAmount"] + pay), "dueAmount": new_due, "paid": settled,
            "status": "paid" if settled else "partial", "updatedAt": now, "settledMode": "wallet",
            "payments": list(b.get("payments") or []) + [{"mode": "wallet", "amount": pay}],
            **({"paidAt": now, "verification": None} if settled else {})}})
        left = money(left - pay)
    remaining = money(max(0.0, member.get("dueAmount") or 0))
    await payment_log(club["id"], "due", amount, "wallet",
                      f"Due paid from wallet · {member.get('name')} · ₹{fmt(amount)}"
                      + ("" if remaining == 0 else f" · ₹{fmt(remaining)} left"),
                      actor=member.get("name", ""), member_id=member["id"])
    return {"member": member_summary(member), "paid": amount, "dueAmount": remaining,
            "walletBalance": money(member.get("walletBalance") or 0),
            "message": f"Paid ₹{fmt(amount)} from wallet" + ("" if remaining == 0 else f" · ₹{fmt(remaining)} still due")}


@router.post("/wallet/topup-requests", status_code=201)
async def request_topup(payload: TopupIn, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    utr = _valid_utr(payload.utr)
    amount = money(payload.amount)
    if amount < 50:
        raise HTTPException(400, "Minimum top-up is ₹50")
    dup = await db.wallet_topup_requests.find_one({"clubId": club["id"], "utr": utr})
    if dup:
        raise HTTPException(400, "That UPI reference was already submitted")
    req = {"id": uid("topup"), "clubId": club["id"], "memberId": member["id"], "memberName": member.get("name", ""),
           "amount": amount, "utr": utr, "status": "pending", "createdAt": now_iso()}
    await db.wallet_topup_requests.insert_one(req)
    req.pop("_id", None)
    await write_log(club["id"], "WARNING", f"Wallet top-up request · {member.get('name')} · ₹{fmt(amount)} · UPI ref {utr} (verify)",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="topup_request", ref_id=req["id"], amount=amount)
    return {"request": req, "message": "Top-up submitted — your balance updates once the counter confirms the payment"}


@router.get("/requests")
async def requests(user: dict = Depends(current_user)):
    """Return the member's pending/approved UPI top-up and plan requests."""
    db, member, club = await member_context(user)
    return await my_requests(db, club, member["id"])


# ------------------------------------------------------------------- frames
@router.get("/frames")
async def frames(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    rows = await my_frames(db, club, member["id"])
    return {"frames": rows, "stats": frame_stats(rows), "dueAmount": money(member.get("dueAmount") or 0)}


@router.get("/rankings")
async def rankings(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    members = await db.members.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    frames = await db.frames.find({"clubId": club["id"]}).to_list(None)
    stats = {m["id"]: {"memberId": m["id"], "name": m.get("name", "Member"), "won": 0, "lost": 0, "frames": 0,
                       "minutes": 0} for m in members}
    for frame in frames:
        winners = set(frame.get("winnersPids") or frame.get("winnerPlayerIds") or [])
        for player in frame.get("players", []):
            mid = player.get("memberId")
            if mid not in stats:
                continue
            stats[mid]["frames"] += 1
            stats[mid]["minutes"] += int(frame.get("durationMinutes") or 0)
            if player.get("pid") in winners or player.get("id") in winners:
                stats[mid]["won"] += 1
            else:
                stats[mid]["lost"] += 1
    rows = list(stats.values())
    for r in rows:
        r["winRate"] = round(r["won"] * 100 / r["frames"]) if r["frames"] else 0
    rows.sort(key=lambda row: (-row["won"], row["lost"], -row["frames"], row["name"].lower()))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
        row["isMe"] = row["memberId"] == member["id"]
    mine = next((row for row in rows if row["isMe"]), None)
    return {"rankings": rows, "me": mine}


# ----------------------------------------------------------------- bookings
@router.get("/bookings")
async def bookings(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    rows = await my_bookings(db, club, member["id"])
    return {"bookings": rows, "serverNow": now_iso(),
            "rules": {"openHour": OPEN_HOUR, "closeHour": CLOSE_HOUR, "closeMin": CLOSE_MIN,
                      "maxDaysAhead": MAX_DAYS_AHEAD}}


@router.get("/bookings/availability")
async def availability(tableId: str, date: str, user: dict = Depends(current_user)):
    """Busy ranges for a table on a club-local day (other members anonymised)."""
    db, member, club = await member_context(user)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date or ""):
        raise HTTPException(400, "date must be YYYY-MM-DD")
    await _sweep(db, club["id"])
    rows = await db.bookings.find({"clubId": club["id"], "tableId": tableId, "dateIST": date,
                                   "status": "upcoming"}).to_list(200)
    busy = [{"start": b.get("startIST"), "end": b.get("endIST"), "mine": b.get("memberId") == member["id"]}
            for b in rows]
    busy.sort(key=lambda b: b["start"] or "")
    now_local = now_utc().astimezone(IST)
    return {"busy": busy, "date": date, "tableId": tableId,
            "nowIST": now_local.strftime("%Y-%m-%d %H:%M"),
            "openHour": OPEN_HOUR, "closeHour": CLOSE_HOUR, "closeMin": CLOSE_MIN}


@router.post("/bookings", status_code=201)
async def create_booking(payload: BookingIn, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    payload.memberId = None
    res = await _create_booking(club["id"], payload, user)
    b = res["booking"]
    await write_log(club["id"], "BILLING", f"Table booked · {member.get('name')} · {b['tableName']} · {b['dateIST']} {b['startIST']}–{b['endIST']}",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="booking", ref_id=b["id"])
    return {"booking": b, "message": f"{b['tableName']} booked for {b['startIST']}–{b['endIST']}"}


@router.delete("/bookings/{booking_id}")
async def cancel_booking(booking_id: str, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    res = await _cancel_booking(club["id"], booking_id, user)
    b = res["booking"]
    await write_log(club["id"], "BILLING", f"Booking cancelled · {member.get('name')} · {b.get('tableName')} · {b.get('dateIST')} {b.get('startIST')}",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="booking", ref_id=booking_id)
    return {"ok": True, "booking": b, "message": "Booking cancelled"}


# -------------------------------------------------------------- tournaments
@router.get("/tournaments")
async def tournaments(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    return {"tournaments": await club_tournaments(db, club, member["id"])}


@router.post("/tournaments/{tour_id}/join", status_code=201)
async def join_tournament(tour_id: str, payload: JoinIn, user: dict = Depends(current_user)):
    """Register for an upcoming event. Entry fee from wallet (paid) or at the counter."""
    db, member, club = await member_context(user)
    t = await db.tournaments.find_one({"id": tour_id, "clubId": club["id"]})
    if not t:
        raise HTTPException(404, "Tournament not found")
    if t.get("status") != "upcoming":
        raise HTTPException(400, "Registrations are closed for this event")
    players = list(t.get("participants") or [])
    if any(p.get("memberId") == member["id"] for p in players):
        raise HTTPException(400, "You're already registered")
    if len(players) >= int(t.get("maxPlayers") or 64):
        raise HTTPException(400, "Sorry, the entry list is full")
    fee = money(t.get("entryFee") or 0)
    mode = (payload.mode or "counter").lower()
    paid = False
    if fee > 0 and mode == "wallet":
        if money(member.get("walletBalance") or 0) < fee:
            raise HTTPException(400, f"Wallet is short for the ₹{fmt(fee)} entry fee — pay at the counter instead")
        await db.members.update_one({"id": member["id"]}, {"$inc": {"walletBalance": -fee}, "$set": {"updatedAt": now_iso()}})
        paid = True
    p = {"pid": uid("tp"), "name": member.get("name", "Member"), "phone": member.get("phone") or "",
         "memberId": member["id"], "paidEntry": paid or fee == 0, "seed": len(players) + 1,
         "entryFee": fee, "registeredVia": "members_app", "registeredAt": now_iso()}
    players.append(p)
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"participants": players}})
    if paid and fee > 0:
        await payment_log(club["id"], "tournaments", fee, "wallet",
                          f"Tournament entry · {p['name']} · {t['name']} (wallet)",
                          actor=p["name"], member_id=member["id"], ref_type="tournament", ref_id=tour_id)
    await write_log(club["id"], "BILLING", f"Tournament registration · {p['name']} · {t['name']}"
                    + (" · paid from wallet" if paid else (" · fee at counter" if fee > 0 else "")),
                    actor=p["name"], member_id=member["id"], ref_type="tournament", ref_id=tour_id)
    fresh = await db.tournaments.find_one({"id": tour_id})
    from ..portal import tournament_view
    return {"tournament": tournament_view(fresh, member["id"]),
            "message": "You're in! " + ("Entry fee paid from wallet." if paid else
                                        (f"Pay the ₹{fmt(fee)} entry fee at the counter." if fee > 0 else ""))}


@router.delete("/tournaments/{tour_id}/join")
async def leave_tournament(tour_id: str, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    t = await db.tournaments.find_one({"id": tour_id, "clubId": club["id"]})
    if not t:
        raise HTTPException(404, "Tournament not found")
    if t.get("status") != "upcoming":
        raise HTTPException(400, "The event has started — ask the counter")
    players = list(t.get("participants") or [])
    mine = next((p for p in players if p.get("memberId") == member["id"]), None)
    if not mine:
        raise HTTPException(400, "You're not registered for this event")
    if mine.get("paidEntry") and money(t.get("entryFee") or 0) > 0:
        raise HTTPException(400, "Entry fee already paid — ask the counter to withdraw and refund")
    players = [p for p in players if p.get("pid") != mine.get("pid")]
    for i, p in enumerate(players, 1):
        p["seed"] = i
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"participants": players}})
    await write_log(club["id"], "BILLING", f"Tournament withdrawal · {member.get('name')} · {t['name']}",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="tournament", ref_id=tour_id)
    return {"ok": True, "message": "Registration withdrawn"}


# --------------------------------------------------------------- membership
@router.get("/membership-plans")
async def membership_plans(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    plans = await db.plans.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    plans.sort(key=lambda p: (p.get("type") != "wallet", money(p.get("amount") or 0)))
    reqs = await my_requests(db, club, member["id"])
    return {"plans": [plan_view(p, member) for p in plans], "club": public_club(club),
            "currentPlanId": member.get("planId"), "membership": membership_status(member),
            "walletBalance": money(member.get("walletBalance") or 0), "requests": reqs["membership"]}


@router.post("/membership-requests", status_code=201)
async def create_membership_request(payload: MembershipRequestIn, user: dict = Depends(current_user)):
    """Paid via the club UPI QR → pending until the counter verifies the UTR."""
    db, member, club = await member_context(user)
    plan = await db.plans.find_one({"id": payload.planId, "clubId": club["id"], "active": {"$ne": False}})
    if not plan:
        raise HTTPException(404, "Membership plan not found")
    utr = _valid_utr(payload.utr)
    existing = await db.membership_requests.find_one({"memberId": member["id"], "planId": plan["id"], "status": "pending"})
    if existing:
        return {"request": _clean(existing), "message": "This request is already pending verification"}
    request = {"id": uid("mreq"), "clubId": club["id"], "memberId": member["id"], "memberName": member.get("name", ""),
               "planId": plan["id"], "planName": plan.get("name", ""), "planType": plan.get("type"),
               "amount": money(plan.get("amount") or 0), "utr": utr, "status": "pending", "createdAt": now_iso()}
    await db.membership_requests.insert_one(request)
    request.pop("_id", None)
    await write_log(club["id"], "WARNING", f"Membership request · {member.get('name')} · {plan.get('name')} · ₹{fmt(plan.get('amount') or 0)} · UPI ref {utr} (verify)",
                    actor=member.get("name", ""), member_id=member["id"], ref_type="membership_request", ref_id=request["id"],
                    amount=money(plan.get("amount") or 0))
    return {"request": request, "message": "Submitted — your plan activates once the counter verifies the payment"}


@router.post("/membership-plans/{plan_id}/buy")
async def buy_plan(plan_id: str, payload: BuyPlanIn, user: dict = Depends(current_user)):
    """Buy a pass / monthly plan straight from the wallet balance."""
    db, member, club = await member_context(user)
    if (payload.mode or "wallet") != "wallet":
        raise HTTPException(400, "Only wallet purchase is instant — use UPI for other modes")
    plan = await db.plans.find_one({"id": plan_id, "clubId": club["id"], "active": {"$ne": False}})
    if not plan:
        raise HTTPException(404, "Membership plan not found")
    if plan.get("type") == "wallet":
        raise HTTPException(400, "Wallet recharge plans are paid via UPI — scan the club QR and submit the reference")
    amount = money(plan.get("amount") or 0)
    wallet = money(member.get("walletBalance") or 0)
    if wallet < amount:
        raise HTTPException(400, f"Wallet is short — ₹{fmt(wallet)} available, plan is ₹{fmt(amount)}")
    member = await db.members.find_one_and_update(
        {"id": member["id"]}, {"$inc": {"walletBalance": -amount}, "$set": {"updatedAt": now_iso()}})
    member, sale, effect = await grant_plan(club, member, plan, mode="wallet",
                                            actor=member.get("name", ""), origin="member_app")
    return {"member": member_summary(member), "sale": sale, "membership": membership_status(member),
            "message": f"{plan.get('name')} activated · {effect}"}


# ------------------------------------------------------------------ activity
@router.get("/activity")
async def activity(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    udoc = await _user_doc(db, user)
    return await my_activity(db, club, member, udoc.get("activitySeenAt"))


@router.post("/activity/seen")
async def activity_seen(payload: SeenIn = None, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    await db.users.update_one({"id": user.get("id")}, {"$set": {"activitySeenAt": now_iso()}})
    return {"ok": True}


# ------------------------------------------------------------------- profile
@router.get("/me")
async def me(user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    udoc = await _user_doc(db, user)
    memberships = []
    for ms in udoc.get("memberships") or []:
        c = ms.get("club") or {}
        memberships.append({"memberId": ms.get("id"), "clubId": ms.get("clubId"), "clubName": c.get("name", "Club"),
                            "current": ms.get("clubId") == club["id"]})
    return {"member": member_summary(member), "club": public_club(club), "memberships": memberships,
            "email": udoc.get("email") or member.get("email") or ""}


@router.patch("/profile")
async def patch_profile(payload: ProfileIn, user: dict = Depends(current_user)):
    db, member, club = await member_context(user)
    ops = {}
    if payload.name is not None:
        ops["name"] = payload.name.strip()
    if payload.phone is not None:
        ops["phone"] = payload.phone.strip()
    if not ops:
        raise HTTPException(400, "Nothing to update")
    ops["updatedAt"] = now_iso()
    member = await db.members.find_one_and_update({"id": member["id"]}, {"$set": ops})
    await db.users.update_one({"id": user.get("id")}, {"$set": {k: v for k, v in ops.items() if k in ("name", "phone", "updatedAt")}})
    return {"member": member_summary(member), "message": "Profile updated"}


@router.post("/switch-club")
async def switch_club(payload: SwitchClubIn, user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    udoc = await _user_doc(db, user)
    target = next((m for m in udoc.get("memberships") or [] if m.get("clubId") == payload.clubId), None)
    if not target:
        raise HTTPException(403, "You're not a member of that club")
    await db.users.update_one({"id": udoc["id"]}, {"$set": {"memberId": target["id"], "clubId": payload.clubId,
                                                          "updatedAt": now_iso()}})
    return {"ok": True, "clubId": payload.clubId, "message": f"Switched to {(target.get('club') or {}).get('name', 'club')}"}
