"""Shared services — tenant guards, 402 gate, logs (the PAYMENT ledger), stats, sweeps."""
from typing import Optional

from fastapi import HTTPException
from datetime import timedelta

from . import db as db_mod
from .auth import is_master
from .util import now_iso, parse_iso, uid, ist_date, money, fmt, iso, now_utc

PAYMENT_SOURCES = ("frames", "items", "memberships", "due", "tournaments")


def wants_web(request) -> bool:
    """React web client detect — explicit `?client=web` / `X-Client: web` wins;
    warna Origin/Referer sniff (Vite :5173 ya den-frontend on Vercel).
    Flutter app ye headers nahi bhejti → canonical (Flutter) shapes intact."""
    if (request.query_params.get("client") or "").lower() == "web":
        return True
    if (request.headers.get("x-client") or "").lower() == "web":
        return True
    hay = f"{request.headers.get('origin', '')} {request.headers.get('referer', '')}"
    return "den-frontend" in hay or ":5173" in hay


def alias_player_ids(doc):
    """Session/frame players carry BOTH `pid` (canonical) and `id` (web alias —
    React web p.id padhti hai; uske bina sabka id undefined hota hai aur ek
    click pe SAARE players winner select ho jate the)."""
    if not doc:
        return doc
    for p in (doc.get("players") or []):
        p.setdefault("id", p.get("pid"))
    return doc


def frame_web_aliases(f: dict) -> dict:
    """Legacy frames ka read-time backfill — v3.21+ frames write-time pe hi
    poora web alias set lete hain; purane records ko raw canonical fields se
    derive karke web shape complete kar do (FramesScreen/WinnerModal kabhi
    ₹0 ya 'undefined' nahi dikhayenge). Idempotent — setdefault only."""
    if not f:
        return f
    players = f.get("players") or []
    label_of = {p.get("pid"): p.get("label", "?") for p in players}
    for s in (f.get("settlements") or []):
        name = label_of.get(s.get("pid"), "")
        s.setdefault("label", name)
        s.setdefault("name", name)
        s.setdefault("playerName", name)
        s.setdefault("memberName", name)  # web settlementLine reads memberName
    wp = [w for w in (f.get("winnersPids") or [])]
    wset = set(wp)
    f.setdefault("winnerPlayerIds", wp)
    f.setdefault("loserPlayerIds", [p.get("pid") for p in players
                                    if p.get("pid") and p.get("pid") not in wset])
    frame_amount = money(f.get("frameAmount") or 0)
    old_due = money(f.get("oldDueAmount") or 0)
    f.setdefault("oldDueBefore", {})
    f.setdefault("oldDuePaid", {})
    f.setdefault("totalAmount", money(frame_amount + old_due))
    lines = f.get("settlements") or []
    paid = money(sum(money(s.get("walletPart") or 0) + money(s.get("passPart") or 0) +
                     money(s.get("cashPart") or 0) + money(s.get("oldDuePart") or 0)
                     for s in lines))
    due = money(sum(money(s.get("duePart") or 0) for s in lines))
    f.setdefault("paidAmount", paid)
    f.setdefault("dueAmount", due)
    f.setdefault("status", "paid" if due <= 0 else ("partial" if paid > 0 else "unpaid"))
    f.setdefault("paymentMode", f.get("mode") or "cash")
    f.setdefault("requestedPaymentMode", f.get("paymentMode"))
    passes = f.get("passApplied") or []
    f.setdefault("passTableCredit", money(sum(p.get("covers") or 0 for p in passes)))
    f.setdefault("passFramesUsed", sum(int(p.get("framesUsed") or 0) for p in passes))
    f.setdefault("passMemberId", passes[0].get("memberId") if passes else None)
    f.setdefault("passMemberName", passes[0].get("name") if passes else None)
    f.setdefault("membershipDiscountPercent", None)
    f.setdefault("membershipMemberName", None)
    return f


def bill_web_aliases(b: dict) -> dict:
    """ItemBill web aliases — total/paidAmount/dueAmount/paymentMode/updatedAt.
    Legacy bill me ye keys nahi thin: due-mode unpaid bill ka dueAmount =
    full amount tha, baaki sab paid=full. Idempotent."""
    if not b:
        return b
    amount = money(b.get("amount") or 0)
    b.setdefault("total", amount)
    if b.get("dueAmount") is None:
        b["dueAmount"] = 0.0 if b.get("paid") else amount
    if b.get("paidAmount") is None:
        b["paidAmount"] = money(amount - money(b["dueAmount"]))
    b["dueAmount"] = money(b["dueAmount"])
    b["paidAmount"] = money(b["paidAmount"])
    b.setdefault("paymentMode", b.get("mode"))
    if b.get("status") not in ("paid", "partial", "unpaid"):
        b["status"] = "paid" if b.get("paid") else "unpaid"
    b.setdefault("updatedAt", b.get("createdAt"))
    return b


async def get_club(user: dict, club_id: str, allow_member: bool = False) -> dict:
    """Tenant guard for /clubs/{id}/… routes.

    Member tokens (members app) are *not* staff: they only get the routes that
    opt in with allow_member=True (bookings). Everything else — member lists,
    bills, logs, reports — is owner/staff only, even though the member's user
    doc lists the club in clubIds.
    """
    db = await db_mod.get_db()
    club = await db.clubs.find_one({"id": club_id})
    if not club:
        raise HTTPException(404, "Club not found")
    if is_master(user):
        return club
    if user.get("role") == "member" and not allow_member:
        raise HTTPException(403, "Members can't access club administration — use the members app")
    if club_id not in (user.get("clubIds") or []) and club.get("ownerUserId") != user["id"]:
        raise HTTPException(403, "You don't have access to this club")
    return club


def subscription_is_active(sub: Optional[dict]) -> bool:
    """Allow access for the non-terminal subscription states shown in the UI:
    pending, trial, active, past_due, paused, keep_current/current.
    Terminal states expired/cancelled remain blocked."""
    if not sub:
        return False
    status = str(sub.get("status") or "").strip().lower().replace(" ", "_")
    allowed = {"pending", "trial", "active", "past_due", "paused", "keep_current", "current"}
    if status not in allowed:
        return False
    expires = str(sub.get("expiresAt") or "").strip()
    if not expires or expires.lower() in {"null", "none", "n/a"}:
        return True
    try:
        if status in {"past_due", "paused"}:
            return True
        return parse_iso(expires) > parse_iso(now_iso())
    except ValueError:
        return False


async def billing_gate(user: dict, club: dict):
    """402 lock — only a non-terminal active/trial-style subscription may mutate billing.

    Prefer the logged-in user's own subscription when they are the owner of the club,
    otherwise fall back to the club owner's record. This avoids false 402s when the UI
    shows the active plan on the account but the club owner lookup is stale or mismatched.
    Staff inherit their assigned club's billing access and do not need an individual
    subscription of their own."""
    if user.get("role") == "staff":
        if club.get("id") not in (user.get("clubIds") or []):
            raise HTTPException(403, "You don't have access to this club")
        return
    if is_master(user):
        return
    if user["id"] == "local-admin":  # AUTH_DISABLED synthetic admin
        return
    db = await db_mod.get_db()
    owner_id = club.get("ownerUserId") or user["id"]
    owner = await db.users.find_one({"id": owner_id})
    current_sub = (user or {}).get("subscription")
    owner_sub = (owner or {}).get("subscription")
    sub = current_sub if user.get("id") == owner_id else owner_sub
    if not sub and current_sub:
        sub = current_sub
    if not subscription_is_active(sub):
        if user.get("id") in (club.get("ownerUserId"), user.get("id")) and subscription_is_active(current_sub):
            return
        if club.get("id") in (user.get("clubIds") or []) and subscription_is_active(current_sub):
            return
        raise HTTPException(402, "Subscription required — choose a plan to keep billing")


def deny_staff_admin(user: dict):
    """Staff lockdown — money-admin surfaces return 403 BEFORE any 402 logic."""
    if user.get("role") == "staff":
        raise HTTPException(403, "Admin area — owner access required")


def deny_staff_mutation(user: dict):
    """require_owner_billing — mutations on money-admin surfaces: staff 403 first."""
    if user.get("role") == "staff":
        raise HTTPException(403, "Admin area — owner access required")


# ---------------------------------------------------------------------- logs
async def write_log(club_id: str, tag: str, message: str, actor: str = "",
                    member_id: str = None, type: str = None, ref_type: str = None,
                    ref_id: str = None, amount: float = None, mode: str = None):
    db = await db_mod.get_db()
    doc = {
        "id": uid("log"), "clubId": club_id, "tag": tag, "message": message,
        "actorName": actor or "system", "createdAt": now_iso(),
    }
    if member_id:
        doc["memberId"] = member_id
    if type:
        doc["type"] = type
    if ref_type:
        doc["refType"] = ref_type
    if ref_id:
        doc["refId"] = ref_id
    if amount is not None:
        doc["amount"] = money(amount)
    if mode:
        doc["mode"] = mode
    await db.logs.insert_one(doc)
    return doc


async def payment_log(club_id: str, source: str, amount: float, mode: str, message: str,
                      actor: str = "", member_id: str = None, ref_type: str = None,
                      ref_id: str = None, date_iso: str = None):
    """The income ledger. `mode: wallet` entries are consumption of pre-paid
    balance — reports exclude them from income (no double counting)."""
    db = await db_mod.get_db()
    doc = {
        "id": uid("log"), "clubId": club_id, "tag": "PAYMENT", "message": message,
        "actorName": actor or "system", "createdAt": date_iso or now_iso(),
        "type": source, "amount": money(amount), "mode": mode,
    }
    if member_id:
        doc["memberId"] = member_id
    if ref_type:
        doc["refType"] = ref_type
    if ref_id:
        doc["refId"] = ref_id
    await db.logs.insert_one(doc)
    return doc


# --------------------------------------------------- plan selling (shared)
async def book_plan_sale(club: dict, member: dict, plan: dict, mode: str,
                         actor: str = "", origin: str = "sell") -> dict:
    """MembershipSale doc + PAYMENT ledger entry (`type: memberships`) — reports
    (day-close / monthly / finance) isi ledger se padhti hain, isliye jo bhi
    flow plan beche (sell route / plan-on-join / member PATCH / late payment)
    SALE ISI SE book hota hai — kabhi koi flow paisa skip nahi karega."""
    db = await db_mod.get_db()
    effect = plan_effect(club, member, plan)[1]  # sirf message ke liye
    sale = {
        "id": uid("sale"), "clubId": club["id"], "memberId": member["id"],
        "memberName": member["name"], "planId": plan["id"], "planName": plan["name"],
        "planType": plan["type"], "amount": money(plan["amount"]), "mode": mode,
        "origin": origin, "createdAt": now_iso(),
    }
    await db.membership_sales.insert_one(sale)
    sale.pop("_id", None)
    await payment_log(club["id"], "memberships", plan["amount"], mode,
                      f"Plan sold · {plan['name']} → {member['name']} ({effect})",
                      actor=actor, member_id=member["id"],
                      ref_type="membership_sale", ref_id=sale["id"])
    return sale


def plan_effect(club: dict, member: dict, plan: dict):
    """(ops, effect_text) — member doc pe lagane wale $set fields."""
    ops = {"planId": plan["id"], "planName": plan["name"], "planType": plan["type"],
           "updatedAt": iso(now_utc())}
    if plan["type"] == "wallet":
        credit = plan.get("value") or plan["amount"]
        ops["walletBalance"] = money((member.get("walletBalance") or 0) + credit)
        effect = f"wallet +₹{fmt(credit)}"
    elif plan["type"] == "pass":
        frames = int(plan.get("frames") or plan.get("value") or 0)
        ops["passFramesLeft"] = int(member.get("passFramesLeft") or 0) + frames
        effect = f"+{frames} frames"
    else:  # monthly
        ops["tableDiscountPercent"] = plan.get("tableDiscountPercent") or 0
        effect = f"{ops['tableDiscountPercent']}% off table money"
    if plan.get("days"):
        ops["planExpiresAt"] = iso(now_utc() + timedelta(days=plan["days"]))
    if plan.get("tableDiscountPercent") is not None:
        ops["tableDiscountPercent"] = plan["tableDiscountPercent"]
    return ops, effect


async def grant_plan(club: dict, member: dict, plan: dict, mode: str = "cash",
                     actor: str = "", book_payment: bool = True,
                     origin: str = "sell"):
    """Benefits apply + (default) sale book — returns (member, sale|None, effect)."""
    db = await db_mod.get_db()
    ops, effect = plan_effect(club, member, plan)
    member = await db.members.find_one_and_update(
        {"id": member["id"]}, {"$set": ops, "$unset": {"expiryMailSentAt": ""}})
    sale = None
    if book_payment:
        sale = await book_plan_sale(club, member, plan, mode, actor=actor, origin=origin)
    return member, sale, effect


# --------------------------------------------------------------------- stats
async def club_stats(club: dict) -> dict:
    """Web AlertsBell/Tables bhi isi se padhte hain — dueLimit, activeMembers,
    currency, today, activeSessions (alias of runningSessions) sab yahan."""
    db = await db_mod.get_db()
    club_id = club["id"]
    logs = await db.logs.find({"clubId": club_id, "tag": "PAYMENT"}).to_list(None)
    today = ist_date(now_iso())
    today_earnings = sum(
        l.get("amount", 0) for l in logs
        if ist_date(l["createdAt"]) == today and l.get("mode") != "wallet"
    )
    today_cash = sum(
        l.get("amount", 0) for l in logs
        if ist_date(l["createdAt"]) == today and l.get("mode") == "cash"
    )
    today_upi = sum(
        l.get("amount", 0) for l in logs
        if ist_date(l["createdAt"]) == today and l.get("mode") == "upi"
    )
    today_start = parse_iso(today + "T00:00:00+05:30")
    tomorrow_start = today_start + timedelta(days=1)
    today_frames = await db.frames.find({
        "clubId": club_id,
        "createdAt": {"$gte": iso(today_start), "$lt": iso(tomorrow_start)},
    }).to_list(None)
    today_wallet = sum(
        settlement.get("walletPart", 0) or 0
        for frame in today_frames
        for settlement in (frame.get("settlements") or [])
    ) + sum(
        l.get("amount", 0) for l in logs
        if ist_date(l["createdAt"]) == today and l.get("mode") == "wallet"
    )
    members = await db.members.find({"clubId": club_id}).to_list(None)
    total_due = sum(m.get("dueAmount", 0) for m in members)
    active_members = sum(1 for m in members if m.get("active") is not False)
    running = await db.sessions.count_documents({"clubId": club_id})
    tournaments = await db.tournaments.find({"clubId": club_id}).to_list(None)
    live_matches = sum(
        1 for t in tournaments for m in (t.get("matches") or [])
        if m.get("status") == "table_live"
    )
    settings = club.get("settings") or {}
    return {
        "clubId": club_id,
        "today": today,
        "todayEarnings": money(today_earnings),
        "todayCash": money(today_cash),
        "todayUpi": money(today_upi),
        "todayWallet": money(today_wallet),
        "totalDue": money(total_due),
        "runningSessions": running,
        "activeSessions": running,  # web alias
        "liveMatches": live_matches,
        "activeMembers": active_members,
        "dueLimit": settings.get("dueLimit", 0),
        "currency": settings.get("currency", "INR"),
        "currencySymbol": settings.get("currencySymbol", "₹"),
    }


def member_badge(m: dict) -> str:
    """Auto type badge: regular | wallet | pass | due | monthly."""
    if (m.get("dueAmount") or 0) > 0:
        return "due"
    if m.get("planType") == "monthly" and not _expired(m):
        return "monthly"
    if (m.get("passFramesLeft") or 0) > 0 and not _expired(m):
        return "pass"
    if (m.get("walletBalance") or 0) > 0:
        return "wallet"
    return "regular"


def _expired(m: dict) -> bool:
    exp = m.get("planExpiresAt")
    if not exp:
        return False
    try:
        return parse_iso(exp) <= parse_iso(now_iso())
    except ValueError:
        return False


def public_member(m: dict) -> dict:
    m = dict(m)
    m["dueAmount"] = money(m.get("dueAmount") or 0)
    m["badge"] = member_badge(m)
    return m


# ------------------------------------------------- membership expiry sweep
async def sweep_expired_memberships(club: dict):
    """Idempotent sweep on /data load — expired membership mail + WARNING log."""
    from .mail import tpl_plan_expired, send_and_record
    db = await db_mod.get_db()
    now = now_iso()
    members = await db.members.find({"clubId": club["id"]}).to_list(None)
    for m in members:
        exp = m.get("planExpiresAt")
        if not exp or m.get("expiryMailSentAt"):
            continue
        try:
            expired = parse_iso(exp) <= parse_iso(now)
        except ValueError:
            continue
        if not expired:
            continue
        await db.members.update_one(
            {"id": m["id"]},
            {"$set": {"expiryMailSentAt": now}},
        )
        await write_log(club["id"], "WARNING",
                        f"Membership expired — {m['name']} ({m.get('planName') or 'plan'})",
                        member_id=m["id"])
        if m.get("email"):
            subj, html = tpl_plan_expired(club.get("name", "Rowdy's Den"), m)
            await send_and_record("plan_expired", m["email"], subj, html,
                                  club_id=club["id"], member_id=m["id"])
