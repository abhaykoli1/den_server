"""★ AUTHORITATIVE BILLING ENGINE — the server is the ONLY calculator.

Locked rules (unit-tested):
  - tableAmount = max(minCharge, round(hourlyRate/60 * minutes, 2)) on the SERVER clock
    (decimal-exact: ₹280/hr x 1m = ₹4.67, never a whole-rupee ceil)
  - Winner never pays. Solo: loser pays all. 2v2: losing team splits evenly.
  - Frame pass first when chosen: FULL hourly rate, one frame per bill,
    due-holders blocked, frame consumed on confirm. Then WALLET -> CASH -> DUE.
  - Old dues first on external member payments (members/{id}/payments).
  - Monthly/premium members take % off TABLE money (their own share).
  - Winner bonus ₹ added to payer side, credited to winners' wallets.
  - Glove charges (unreturned) join AFTER every discount — never discountable.
  - billingLock atomic guard — a session can never be billed twice.
"""
from fastapi import HTTPException

from .util import ceil_minutes, fmt, money, split_evenly, now_iso, parse_iso, ist_hour


def peak_active(start_h, end_h, at_iso) -> bool:
    if start_h is None or end_h is None or start_h == end_h:
        return False
    h = ist_hour(at_iso)
    if start_h < end_h:
        return start_h <= h < end_h
    return h >= start_h or h < end_h  # wrap-around window (e.g. 18 -> 2)


def resolve_hourly(rate: dict, player_count: int, at_iso=None):
    """(hourlyRate, isPeak). Peak window overrides per-player rates."""
    at_iso = at_iso or now_iso()
    ph = rate.get("peakHourlyRate")
    if ph:
        if peak_active(rate.get("peakStartHour"), rate.get("peakEndHour"), at_iso):
            return float(ph), True
    rbp = rate.get("ratesByPlayers") or {}
    keyed = rbp.get(str(player_count))
    if keyed:
        return float(keyed), False
    return float(rate.get("hourlyRate", 0)), False


def table_amount(hourly: float, minutes: int, min_charge: float) -> float:
    return max(money(min_charge or 0), money(hourly / 60 * minutes))


def _expired(m: dict) -> bool:
    exp = m.get("planExpiresAt")
    if not exp:
        return False
    try:
        return parse_iso(exp) <= parse_iso(now_iso())
    except ValueError:
        return False


def membership_pct(member: dict, club: dict) -> float:
    """% off TABLE money for active monthly/premium members."""
    if member.get("planType") != "monthly" or _expired(member):
        return 0.0
    if member.get("tableDiscountPercent") is not None:
        return float(member.get("tableDiscountPercent") or 0)
    return float((club.get("settings") or {}).get("monthlyTableDiscount", 0))


def pass_block_reason(member: dict):
    if (member.get("dueAmount") or 0) > 0:
        return "pending due"
    if member.get("planType") != "pass" or _expired(member):
        return "no active frame pass"
    if (member.get("passFramesLeft") or 0) < 1:
        return "no frames left"
    return None


def resolve_sides(players: list, match_mode: str, winners: list, winning_team):
    """Return (winners, losers) player dicts; 400 readable on any problem."""
    if match_mode == "2v2":
        if winning_team not in ("A", "B"):
            raise HTTPException(400, "Pick the winning team before confirming")
        winners_p = [p for p in players if p.get("team") == winning_team]
        losers_p = [p for p in players if p.get("team") and p.get("team") != winning_team]
        if not winners_p or not losers_p:
            raise HTTPException(400, "Both teams need at least one player")
        return winners_p, losers_p
    wset = set(winners or [])
    winners_p = [p for p in players if p["pid"] in wset]
    if not winners_p:
        raise HTTPException(400, "Pick the winner before confirming the bill")
    losers_p = [p for p in players if p["pid"] not in wset]
    if not losers_p:
        raise HTTPException(400, "Someone has to pay — the winner can't be everyone")
    return winners_p, losers_p


def compute_bill(session: dict, club: dict, members_map: dict, losers: list,
                 winners: list, discount: float, cash_paid: float, use_pass: list):
    """Pure math + validation. Raises 400 BEFORE anything is locked/mutated."""
    minutes = ceil_minutes(session["startedAt"], session["endedAt"])
    hourly = float(session.get("hourlyRate", 0))
    min_charge = float(session.get("minCharge", 0) or 0)
    t_amt = table_amount(hourly, minutes, min_charge)

    items = session.get("items") or []
    items_amt = money(sum(i.get("amount", 0) for i in items))
    gloves = [g for g in (session.get("gloves") or []) if not g.get("returned")]
    glove_amt = money(sum(g.get("price", 0) for g in gloves))
    wb = float((club.get("settings") or {}).get("winnerBonus", 0)) if winners else 0.0

    pre_glove = money(t_amt + items_amt + wb)
    if discount and discount > pre_glove:
        raise HTTPException(400, f"Discount can't exceed the bill (₹{fmt(pre_glove)})")

    n = len(losers)
    t_sh = split_evenly(t_amt, n)
    i_sh = split_evenly(items_amt, n)
    wb_sh = split_evenly(wb, n)
    d_sh = split_evenly(discount or 0, n)
    g_sh = split_evenly(glove_amt, n)

    pass_set = set(use_pass or [])
    advance = float(session.get("advancePaid", 0) or 0)
    cash_pool = money(advance + (cash_paid or 0))

    settings = club.get("settings") or {}
    due_limit = float(settings.get("dueLimit", 0) or 0)

    lines, mem_discount_total, dues_over = [], 0.0, []
    membership_applied = []  # [{memberId, name, percent}] — web frame aliases
    for idx, p in enumerate(losers):
        member = members_map.get(p.get("memberId") or "")
        pct = membership_pct(member, club) if member else 0.0
        m_disc = money(t_sh[idx] * pct / 100)
        mem_discount_total += m_disc
        if member and m_disc > 0:
            membership_applied.append({"memberId": member["id"], "name": member["name"],
                                       "percent": pct})
        charge = money(max(0.0, t_sh[idx] - m_disc - d_sh[idx]) + i_sh[idx]
                       + wb_sh[idx] + g_sh[idx])
        line = {
            "pid": p["pid"], "label": p["label"], "memberId": p.get("memberId"),
            "name": p["label"], "playerName": p["label"],  # web aliases
            "memberName": member["name"] if member else p["label"],  # web settlementLine
            "charge": charge, "membershipDiscount": m_disc,
            "walletPart": 0.0, "passPart": 0.0, "cashPart": 0.0, "duePart": 0.0,
            "oldDuePart": 0.0,
        }
        # 1) frame pass, when the counter chose it
        if member and member["id"] in pass_set:
            reason = pass_block_reason(member)
            if reason:
                raise HTTPException(400, f"{member['name']} can't use a frame pass — {reason}")
            cover = money(min(charge, hourly))
            line["passPart"] = cover
            charge = money(charge - cover)
        # 2) wallet first
        if member and charge > 0 and (member.get("walletBalance") or 0) > 0:
            w = money(min(float(member["walletBalance"]), charge))
            line["walletPart"] = w
            charge = money(charge - w)
        # 3) cash pool (advance + paid-in now)
        if charge > 0 and cash_pool > 0:
            c = money(min(cash_pool, charge))
            line["cashPart"] = c
            cash_pool = money(cash_pool - c)
            charge = money(charge - c)
        # 4) overflow -> due (members only)
        if charge > 0:
            if member:
                line["duePart"] = charge
                if due_limit and float(member.get("dueAmount", 0)) + charge > due_limit:
                    dues_over.append(member["name"])
            else:
                raise HTTPException(
                    400, f"Cash short for {p['label']} — guests can't carry due. "
                         f"Collect ₹{fmt(charge)} more")
        lines.append(line)

    cash_used = money(sum(l["cashPart"] for l in lines))
    advance_used = money(min(advance, cash_used))
    collected_now = money(cash_used - advance_used)

    # --- old-due harvest ---------------------------------------------------
    # Web FinalBillCard ka estimate `frameAmount + oldDue` hota hai aur
    # cashPaid me losers ke PURANE dues bhi shamil aate hain. Frame-share
    # cover hone ke baad jo pool bachta hai wo losers ke old dues chhooota
    # karta hai — warna wo paisa "extra held" note me kho jata tha aur
    # Day Close hamesha short dikhta tha (MONEY LEAK fix).
    old_due_before: dict = {}
    old_due_paid: dict = {}
    for line in lines:
        if cash_pool <= 0:
            break
        mid = line.get("memberId")
        member = members_map.get(mid or "")
        if not member:
            continue
        pending = money(member.get("dueAmount") or 0)
        if pending <= 0:
            continue
        old_due_before.setdefault(mid, pending)
        pay = money(min(pending - money(old_due_paid.get(mid, 0.0)), cash_pool))
        if pay <= 0:
            continue
        line["oldDuePart"] = money(line["oldDuePart"] + pay)
        old_due_paid[mid] = money(old_due_paid.get(mid, 0.0) + pay)
        cash_pool = money(cash_pool - pay)
    old_due_amount = money(sum(old_due_paid.values()))
    # Advance-funded harvest pahle hi ledger me gina ja chuka hai (advance
    # collect-time pe "frames" book hota hai) — sirf AB aaya cash "due"
    # income banta hai, warna double count hoga. oldDueNow = ledger part.
    advance_left = money(max(0.0, advance - advance_used))
    due_now = money(max(0.0, old_due_amount - money(min(advance_left, old_due_amount))))

    leftover = cash_pool  # over-payment held at the counter
    frame_amount = money(
        sum(l["walletPart"] + l["passPart"] + l["cashPart"] + l["duePart"] for l in lines)
    )

    notes = []
    if leftover > 0:
        notes.append(f"extra ₹{fmt(leftover)} held — refund manually")
    if dues_over:
        notes.append(f"due limit crossed for {', '.join(dues_over)}")
    if gloves:
        notes.append(f"gloves not returned +₹{fmt(glove_amt)} "
                     f"({', '.join(g['label'] for g in gloves)})")

    return {
        "minutes": minutes, "hourlyRate": hourly, "minCharge": min_charge,
        "tableAmount": t_amt, "itemsAmount": items_amt, "items": items,
        "membershipDiscount": money(mem_discount_total),
        "winnerBonus": wb, "discount": money(discount or 0),
        "gloves": gloves, "gloveCharges": glove_amt,
        "frameAmount": frame_amount,
        "settlements": lines,
        "passApplied": [
            {"memberId": l["memberId"], "name": l["label"],
             "framesUsed": 1, "covers": l["passPart"]}
            for l in lines if l["passPart"] > 0
        ],
        "membershipApplied": membership_applied,
        "advanceUsed": advance_used, "collectedNow": collected_now,
        "oldDueAmount": old_due_amount, "oldDueNow": due_now,
        "oldDueBefore": old_due_before, "oldDuePaid": old_due_paid,
        "cashPoolLeft": leftover, "notes": notes,
    }


def calc_web_aliases(calc: dict, winners: list, losers: list,
                     mode: str = None, requested: str = None) -> dict:
    """Web FrameRecord ka poora alias set — confirm pe write-time aur winner
    correction pe update dono jagah SAME keys (FramesScreen/WinnerModal isi
    se padhti hai). Sab derivations raw canonical fields se — additive only."""
    lines = calc.get("settlements") or []
    frame_amount = money(calc.get("frameAmount") or 0)
    old_due = money(calc.get("oldDueAmount") or 0)
    paid = money(sum(money(l.get("walletPart") or 0) + money(l.get("passPart") or 0) +
                     money(l.get("cashPart") or 0) + money(l.get("oldDuePart") or 0)
                     for l in lines))
    due = money(sum(money(l.get("duePart") or 0) for l in lines))
    passes = calc.get("passApplied") or []
    memb = calc.get("membershipApplied") or []
    out = {
        "totalAmount": money(frame_amount + old_due),
        "paidAmount": paid, "dueAmount": due,
        "status": "paid" if due <= 0 else ("partial" if paid > 0 else "unpaid"),
        "winnerPlayerIds": [w["pid"] for w in winners],
        "loserPlayerIds": [l["pid"] for l in losers],
        "oldDueAmount": old_due,
        "oldDueBefore": dict(calc.get("oldDueBefore") or {}),
        "oldDuePaid": dict(calc.get("oldDuePaid") or {}),
        "passTableCredit": money(sum(p.get("covers") or 0 for p in passes)),
        "passFramesUsed": sum(int(p.get("framesUsed") or 0) for p in passes),
        "passMemberId": passes[0].get("memberId") if passes else None,
        "passMemberName": passes[0].get("name") if passes else None,
        "membershipDiscountPercent": (max((m["percent"] for m in memb), default=None)
                                      if memb else None),
        "membershipMemberName": (", ".join(m["name"] for m in memb) if memb else None),
    }
    if mode is not None:
        out["paymentMode"] = mode
        out["requestedPaymentMode"] = requested or mode
    return out


async def apply_settlements(db, club_id: str, calc: dict):
    """Wallet debits, pass consumption, dues, winner-bonus credits."""
    for line in calc["settlements"]:
        mid = line.get("memberId")
        if not mid:
            continue
        ops = {}
        if line["walletPart"]:
            ops["walletBalance"] = money(-line["walletPart"])
        if line["duePart"]:
            ops["dueAmount"] = money(line["duePart"])
        if line["passPart"]:
            ops["passFramesLeft"] = -1
        if ops:
            await db.members.update_one({"id": mid, "clubId": club_id}, {"$inc": ops})
    # winner bonus -> winners' wallets (split evenly)
    return


async def reverse_frame(db, frame: dict):
    """Full reversal for winner correction: wallet refunds, due rollback, pass restore,
    winner-bonus claw-back, and the frame's cash PAYMENT log entries removed."""
    club_id = frame["clubId"]
    for line in frame.get("settlements", []):
        mid = line.get("memberId")
        if not mid:
            continue
        ops = {}
        if line.get("walletPart"):
            ops["walletBalance"] = money(line["walletPart"])
        if line.get("duePart"):
            ops["dueAmount"] = money(-line["duePart"])
        if line.get("oldDuePart"):
            ops["dueAmount"] = money(ops.get("dueAmount", 0.0) + money(line["oldDuePart"]))
        if line.get("passPart"):
            ops["passFramesLeft"] = 1
        if ops:
            await db.members.update_one({"id": mid, "clubId": club_id}, {"$inc": ops})
    for credit in frame.get("winnerCredits", []):
        if credit.get("memberId") and credit.get("amount"):
            await db.members.update_one(
                {"id": credit["memberId"], "clubId": club_id},
                {"$inc": {"walletBalance": money(-credit["amount"])}},
            )
    await db.logs.delete_many({"clubId": club_id, "tag": "PAYMENT",
                               "refType": "frame", "refId": frame["id"]})
    # clamp dues / wallets that reversal could push negative
    await db.members.update_many({"clubId": club_id, "dueAmount": {"$lt": 0}},
                                 {"$set": {"dueAmount": 0}})
    await db.members.update_many({"clubId": club_id, "walletBalance": {"$lt": 0}},
                                 {"$set": {"walletBalance": 0}})
