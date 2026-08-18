"""Frames history + winner correction (full reversal → re-bill → ADMIN log)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..billing import (calc_web_aliases, compute_bill, resolve_sides,
                       reverse_frame)
from ..models import WinnersPatchIn
from ..services import (alias_player_ids, billing_gate, frame_web_aliases,
                        get_club, payment_log, write_log)
from ..util import fmt, money, now_iso, split_evenly

router = APIRouter(prefix="/clubs/{club_id}/frames", tags=["frames"])


@router.get("")
async def list_frames(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    frames = await db.frames.find({"clubId": club["id"]},
                                  sort=[("createdAt", -1)]).to_list(300)
    # read-time backfill: purane frames ki settlements me name/memberName nahi tha
    # aur web alias keys (totalAmount/paidAmount/…) missing thin — derive kar do
    for f in frames:
        alias_player_ids(f)  # players pe id=pid alias (web winner-modal fix)
        frame_web_aliases(f)
    return frames


@router.patch("/{frame_id}/winners")
async def correct_winners(club_id: str, frame_id: str, payload: WinnersPatchIn,
                          user: dict = Depends(current_user)):
    """Full reversal (wallet refunds, due rollback, pass restore, bonus claw-back,
    payment-ledger removal for the frame) then a fresh authoritative re-bill."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    frame = await db.frames.find_one({"id": frame_id, "clubId": club["id"]})
    if not frame:
        raise HTTPException(404, "Frame not found")

    winners, losers = resolve_sides(frame["players"], frame.get("matchMode", "solo"),
                                    payload.winners, payload.winningTeam)
    old_winners = list(frame.get("winners") or [])
    if set(payload.winners) == set(frame.get("winnersPids") or []) and not payload.winningTeam:
        raise HTTPException(400, "That's the same winner — nothing to correct")

    # 1) reverse everything the old bill did
    await reverse_frame(db, frame)

    # 2) fresh member docs post-reversal
    member_ids = {p.get("memberId") for p in frame["players"] if p.get("memberId")}
    members_map = {}
    for mid in member_ids:
        m = await db.members.find_one({"id": mid, "clubId": club["id"]})
        if m:
            members_map[mid] = m

    # 3) rebuild a session-shaped doc with the ORIGINAL economics
    use_pass = payload.usePass
    if use_pass is None:
        use_pass = [p["memberId"] for p in frame.get("passApplied", [])]
    pseudo = {
        "startedAt": frame["startedAt"], "endedAt": frame["endedAt"],
        "hourlyRate": frame.get("hourlyRate", 0), "minCharge": frame.get("minCharge", 0),
        "items": frame.get("items") or [], "gloves": frame.get("gloves") or [],
        "advancePaid": frame.get("advancePaid", 0), "players": frame["players"],
    }
    calc = compute_bill(pseudo, club, members_map, losers, winners,
                        frame.get("discount", 0), frame.get("cashCollected", 0),
                        use_pass)

    # 4) apply new settlements
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
            await db.members.update_one({"id": mid, "clubId": club["id"]}, {"$inc": ops})
    # old dues harvested afresh from the corrected cash pool
    if calc["oldDueAmount"] > 0:
        for mid, paid_now in (calc.get("oldDuePaid") or {}).items():
            if paid_now > 0:
                await db.members.update_one({"id": mid, "clubId": club["id"]},
                                            {"$inc": {"dueAmount": money(-paid_now)}})
        await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}},
                                     {"$set": {"dueAmount": 0}})
    winner_credits = []
    if calc["winnerBonus"] > 0:
        winners_m = [w for w in winners if w.get("memberId") in members_map]
        if winners_m:
            shares = split_evenly(calc["winnerBonus"], len(winners_m))
            for i, w in enumerate(winners_m):
                await db.members.update_one(
                    {"id": w["memberId"], "clubId": club["id"]},
                    {"$inc": {"walletBalance": shares[i]}})
                winner_credits.append({"pid": w["pid"], "memberId": w["memberId"],
                                       "label": w["label"], "amount": shares[i]})
    if calc["collectedNow"] > 0:
        await payment_log(club["id"], "frames", calc["collectedNow"], "cash",
                          f"Frame (corrected) · {frame.get('tableName', '')} · "
                          f"₹{fmt(calc['collectedNow'])}",
                          actor=user.get("name", ""), ref_type="frame", ref_id=frame["id"])
    if calc["oldDueNow"] > 0:
        names = ", ".join(
            f"{members_map[mid]['name']} ₹{fmt(amt)}"
            for mid, amt in (calc.get("oldDuePaid") or {}).items()
            if mid in members_map and amt > 0)
        await payment_log(club["id"], "due", calc["oldDueNow"], "cash",
                          f"Due collected with frame (corrected) · "
                          f"{frame.get('tableName', '')} · {names}",
                          actor=user.get("name", ""), ref_type="frame",
                          ref_id=frame["id"])

    # 5) patch the frame
    win_pids = {w["pid"] for w in winners}
    players_out = []
    for p in frame["players"]:
        p2 = dict(p)
        p2["isWinner"] = p2["pid"] in win_pids
        players_out.append(p2)
    update = {
        "players": players_out,
        "winners": [w["label"] for w in winners],
        "winnersPids": [w["pid"] for w in winners],
        "losers": [l["label"] for l in losers],
        "tableAmount": calc["tableAmount"],
        "membershipDiscount": calc["membershipDiscount"],
        "winnerBonus": calc["winnerBonus"], "gloveCharges": calc["gloveCharges"],
        "gloves": calc["gloves"], "frameAmount": calc["frameAmount"],
        "settlements": calc["settlements"], "passApplied": calc["passApplied"],
        "winnerCredits": winner_credits, "advanceUsed": calc["advanceUsed"],
        "cashCollected": calc["collectedNow"],
        "correctedAt": now_iso(), "correctedBy": user.get("name", ""),
    }
    # web alias set re-derive (old-due harvest bhi naye pool se dobara hua)
    update.update(calc_web_aliases(
        calc, winners, losers,
        mode=frame.get("paymentMode"),
        requested=frame.get("requestedPaymentMode")))
    if payload.note:
        update["correctionNote"] = payload.note
    frame = await db.frames.find_one_and_update({"id": frame_id}, {"$set": update})
    frame.pop("_id", None)
    alias_player_ids(frame)  # web Change-Winner modal p.id se select karti hai
    await write_log(club["id"], "ADMIN",
                    f"Winner corrected · {', '.join(old_winners)} → "
                    f"{', '.join(frame['winners'])} · ₹{fmt(calc['frameAmount'])}"
                    + (f" · {payload.note}" if payload.note else ""),
                    actor=user.get("name", ""), ref_type="frame", ref_id=frame["id"])
    return {"frame": frame,
            "message": f"Winner corrected · re-billed ₹{fmt(calc['frameAmount'])}"}
