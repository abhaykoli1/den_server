"""Live session engine — start → running → stop(preview) → confirm | resume | cancel.
Plus: live item attach, mid-session advances (= real ledger money), notes, table
moves (rate re-resolve), glove tracking, atomic billingLock."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db as db_mod
from ..auth import current_user
from ..billing import (calc_web_aliases, compute_bill, resolve_hourly,
                       resolve_sides, table_amount)
from ..models import (GloveReturnIn, SessionAdvanceIn, SessionConfirmIn,
                      SessionItemsIn, SessionMoveIn, SessionPatchIn,
                      SessionStartIn)
from ..services import (alias_player_ids, billing_gate, get_club, payment_log,
                        wants_web, write_log)
from ..util import (ceil_minutes, fmt, money, now_iso, split_evenly, uid)

router = APIRouter(prefix="/clubs/{club_id}/sessions", tags=["sessions"])


async def _busy_tables(db, club_id: str) -> set:
    sessions = await db.sessions.find({"clubId": club_id}).to_list(None)
    busy = {s["tableId"] for s in sessions}
    tournaments = await db.tournaments.find({"clubId": club_id}).to_list(None)
    for t in tournaments:
        for m in t.get("matches") or []:
            if m.get("status") == "table_live" and m.get("tableId"):
                busy.add(m["tableId"])
    return busy


def _preview(session: dict) -> dict:
    if not session.get("endedAt"):
        end = now_iso()
    else:
        end = session["endedAt"]
    minutes = ceil_minutes(session["startedAt"], end)
    amt = table_amount(float(session.get("hourlyRate", 0)), minutes,
                       float(session.get("minCharge", 0) or 0))
    items_amt = money(sum(i.get("amount", 0) for i in session.get("items") or []))
    gloves = money(sum(g.get("price", 0) for g in (session.get("gloves") or [])
                       if not g.get("returned")))
    return {"minutes": minutes, "tableAmount": amt, "itemsAmount": items_amt,
            "gloveCharges": gloves, "estimate": money(amt + items_amt + gloves)}


@router.get("")
async def list_sessions(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    sessions = await db.sessions.find({"clubId": club["id"]}).to_list(None)
    tables = {t["id"]: t for t in await db.tables.find({"clubId": club["id"]}).to_list(None)}
    out = []
    for s in sessions:
        s = dict(s)
        s["tableName"] = (tables.get(s["tableId"]) or {}).get("name", "?")
        s["preview"] = _preview(s)
        alias_player_ids(s)
        out.append(s)
    return out


@router.post("", status_code=201)
async def start_session(club_id: str, payload: SessionStartIn,
                        user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    table = await db.tables.find_one({"id": payload.tableId, "clubId": club["id"]})
    if not table or not table.get("active", True):
        raise HTTPException(404, "Table not found (or inactive)")
    if payload.tableId in await _busy_tables(db, club["id"]):
        raise HTTPException(400, f"{table['name']} is already live")
    members_map = {}
    players = []
    for seat, p in enumerate(payload.players):
        pid = uid("p")
        player = {"pid": pid, "id": pid,  # id = web alias (React p.id padhti hai)
                  "seat": seat, "label": p.label.strip(),
                  "type": p.type, "isWinner": False}
        if p.team:
            player["team"] = p.team
        if p.type == "member":
            if not p.memberId:
                raise HTTPException(400, f"Seat {seat + 1}: pick the member")
            member = await db.members.find_one({"id": p.memberId, "clubId": club["id"]})
            if not member:
                raise HTTPException(404, f"Member not found for seat {seat + 1}")
            player["memberId"] = member["id"]
            player["label"] = member["name"]
            members_map[member["id"]] = member
        players.append(player)
    member_ids = [p["memberId"] for p in players if p.get("memberId")]
    if len(set(member_ids)) != len(member_ids):
        raise HTTPException(400, "Same member can't occupy two seats — add one as Guest")
    if payload.matchMode == "2v2":
        a = [p for p in players if p.get("team") == "A"]
        b = [p for p in players if p.get("team") == "B"]
        if not a or not b:
            raise HTTPException(400, "2v2 needs players in both Team A and Team B")
    hourly, peak = resolve_hourly(table["rate"], len(players))
    gloves = []
    if payload.gloveSeatIndexes:
        price = float(table["rate"].get("glovePrice") or 0)
        if price <= 0:
            raise HTTPException(400, "Gloves aren't enabled for this table — set a glove price")
        for seat in payload.gloveSeatIndexes:
            if 0 <= seat < len(players):
                pl = players[seat]
                gloves.append({"playerId": pl["pid"], "label": pl["label"],
                               "memberId": pl.get("memberId"), "price": price,
                               "returned": False})
    session = {
        "id": uid("s"), "clubId": club["id"], "tableId": table["id"],
        "tableName": table["name"], "startedAt": now_iso(), "endedAt": None,
        "players": players, "playerCount": len(players),
        "hourlyRate": hourly, "minCharge": float(table["rate"].get("minCharge") or 0),
        "peak": peak, "matchMode": payload.matchMode,
        "items": [], "itemsTotal": 0, "discount": 0,
        "advancePaid": money(payload.advancePaid), "notes": payload.notes.strip(),
        "billingLock": False, "billedBy": None,
    }
    if gloves:
        session["gloves"] = gloves
    await db.sessions.insert_one(session)
    if payload.advancePaid > 0:
        await payment_log(club["id"], "frames", payload.advancePaid, payload.advanceMode,
                          f"Advance · {table['name']} · ₹{fmt(payload.advancePaid)}",
                          actor=user.get("name", ""), ref_type="frame", ref_id=None)
    await write_log(club["id"], "BILLING",
                    f"Table started · {table['name']} · {len(players)} player(s)"
                    + (" · peak rate" if peak else ""),
                    actor=user.get("name", ""))
    session.pop("_id", None)
    session["preview"] = _preview(session)
    return session


async def _get_session(db, club_id: str, session_id: str) -> dict:
    s = await db.sessions.find_one({"id": session_id, "clubId": club_id})
    if not s:
        raise HTTPException(404, "Session not found")
    return alias_player_ids(s)


@router.post("/{session_id}/stop")
async def stop_session(club_id: str, session_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("endedAt"):
        raise HTTPException(400, "Timer is already stopped")
    s = await db.sessions.find_one_and_update({"id": session_id},
                                              {"$set": {"endedAt": now_iso()}})
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.post("/{session_id}/resume")
async def resume_session(club_id: str, session_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("billingLock"):
        # mid-bill resume -> unlock so the table can never brick
        await db.sessions.update_one({"id": session_id},
                                     {"$set": {"billingLock": False, "endedAt": None}})
        raise HTTPException(400, "Session resumed — run the bill again")
    if not s.get("endedAt"):
        raise HTTPException(400, "Timer is already running")
    s = await db.sessions.find_one_and_update({"id": session_id},
                                              {"$set": {"endedAt": None}})
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


async def _attach_items(db, club_id: str, s: dict, lines: list) -> dict:
    """Attach (menuItemId, qty) lines to a session — atomic stock decrement,
    merged line rows, itemsTotal refreshed. Raises readable 400/404.
    Caller validates timer state (running vs confirm-time attach)."""
    for mid, qty in lines:
        stock = await db.menu_items.find_one_and_update(
            {"id": mid, "clubId": club_id, "active": True,
             "stockQty": {"$gte": qty}},
            {"$inc": {"stockQty": -qty}}, after=False)
        if not stock:
            item = await db.menu_items.find_one({"id": mid, "clubId": club_id})
            if not item or not item.get("active", True):
                raise HTTPException(404, "Item not found (or inactive)")
            raise HTTPException(400, f"Only {item.get('stockQty', 0)} {item['name']} left in stock")
        price = float(stock["price"])
        cost = float(stock.get("costPrice") or 0)
        items = list(s.get("items") or [])
        merged = next((i for i in items if i["menuItemId"] == mid), None)
        if merged:
            merged["qty"] += qty
            merged["amount"] = money(merged["qty"] * merged["price"])
            merged["costAmount"] = money(merged["qty"] * merged.get("costPrice", 0))
        else:
            items.append({"menuItemId": stock["id"], "itemId": stock["id"],  # itemId = web alias
                          "name": stock["name"],
                          "price": price, "qty": qty,
                          "amount": money(price * qty),
                          "costPrice": cost, "costAmount": money(cost * qty)})
        total = money(sum(i["amount"] for i in items))
        s = await db.sessions.find_one_and_update(
            {"id": s["id"]}, {"$set": {"items": items, "itemsTotal": total}})
    return s


@router.post("/{session_id}/items")
async def add_item(club_id: str, session_id: str, payload: SessionItemsIn,
                   user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("endedAt"):
        raise HTTPException(400, "Items can only be added while the timer is running")
    s = await _attach_items(db, club["id"], s, payload.lines())
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.post("/{session_id}/advance")
async def add_advance(club_id: str, session_id: str, payload: SessionAdvanceIn,
                      user: dict = Depends(current_user)):
    """Mid-session advance = real ledger money NOW; settled inside the cash pool
    at confirm time."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("endedAt"):
        raise HTTPException(400, "Timer is stopped — advances go in while running")
    s = await db.sessions.find_one_and_update(
        {"id": session_id}, {"$inc": {"advancePaid": money(payload.amount)}})
    await payment_log(club["id"], "frames", payload.amount, payload.mode,
                      f"Advance · {s.get('tableName', 'table')} · ₹{fmt(payload.amount)}",
                      actor=user.get("name", ""), ref_type="frame", ref_id=None)
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.patch("/{session_id}")
async def patch_session(club_id: str, session_id: str, payload: SessionPatchIn,
                        user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    s = await db.sessions.find_one_and_update({"id": session_id},
                                              {"$set": {"notes": payload.notes.strip()}})
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.post("/{session_id}/move")
async def move_session(club_id: str, session_id: str, payload: SessionMoveIn,
                       user: dict = Depends(current_user)):
    """Move to a free table — rate + peak + minCharge re-resolve, timer untouched."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    target = await db.tables.find_one({"id": payload.tableId, "clubId": club["id"]})
    if not target or not target.get("active", True):
        raise HTTPException(404, "Target table not found (or inactive)")
    if payload.tableId in await _busy_tables(db, club["id"]):
        raise HTTPException(400, f"{target['name']} is busy right now")
    hourly, peak = resolve_hourly(target["rate"], s.get("playerCount", len(s["players"])))
    s = await db.sessions.find_one_and_update(
        {"id": session_id},
        {"$set": {"tableId": target["id"], "tableName": target["name"],
                  "hourlyRate": hourly, "peak": peak,
                  "minCharge": float(target["rate"].get("minCharge") or 0)}})
    await write_log(club["id"], "BILLING", f"Session moved → {target['name']}",
                    actor=user.get("name", ""))
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.post("/{session_id}/gloves/return")
async def return_glove(club_id: str, session_id: str, payload: GloveReturnIn,
                       user: dict = Depends(current_user)):
    """Toggle a glove back — live or stopped-unbilled. Idempotent."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("billingLock"):
        raise HTTPException(400, "Bill is already being processed")
    gloves = s.get("gloves") or []
    glove = next((g for g in gloves if g["playerId"] == payload.playerId), None)
    if not glove:
        raise HTTPException(404, "That player has no glove out")
    if glove.get("returned") != payload.returned:
        glove["returned"] = payload.returned
        await db.sessions.update_one({"id": session_id}, {"$set": {"gloves": gloves}})
        await write_log(club["id"],
                        "BILLING" if payload.returned else "WARNING",
                        (f"Glove returned · {glove['label']}" if payload.returned
                         else f"Glove back out · {glove['label']}"),
                        actor=user.get("name", ""))
    s["gloves"] = gloves
    s["preview"] = _preview(s)
    s.pop("_id", None)
    return s


@router.post("/{session_id}/confirm")
async def confirm_session(request: Request, club_id: str, session_id: str, payload: SessionConfirmIn,
                          user: dict = Depends(current_user)):
    """★ The authoritative bill. All validation runs BEFORE the atomic lock."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("billingLock"):
        raise HTTPException(400, "This session is already billed")
    if not s.get("endedAt"):
        raise HTTPException(400, "Stop the timer first, then confirm the bill")

    # --- confirm-time item attach (web final-bill chips) — pre-lock, atomic ---
    if payload.items:
        s = await _attach_items(db, club["id"], s,
                                [(i.menuItemId, i.qty) for i in payload.items if i.menuItemId])

    # --- resolve sides + member docs (pre-lock validation) ----------------
    winners, losers = resolve_sides(s["players"], s.get("matchMode", "solo"),
                                    payload.winners, payload.winningTeam)
    member_ids = {p.get("memberId") for p in s["players"] if p.get("memberId")}
    members_map = {}
    for mid in member_ids:
        m = await db.members.find_one({"id": mid, "clubId": club["id"]})
        if m:
            members_map[mid] = m
        else:
            raise HTTPException(400, "A player member no longer exists — cancel and restart")
    calc = compute_bill(s, club, members_map, losers, winners,
                        payload.discount, payload.cashPaid, payload.usePass)

    # --- atomic lock (lock-time doc is the billing source) ----------------
    locked = await db.sessions.find_one_and_update(
        {"id": session_id, "clubId": club["id"],
         "$or": [{"billingLock": {"$exists": False}}, {"billingLock": False}]},
        {"$set": {"billingLock": True, "billedBy": user.get("name", "")}})
    if not locked:
        raise HTTPException(400, "This session is already billed")
    s = locked

    # --- apply settlements ------------------------------------------------
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
    # old dues harvested from the losers' cash pool (web bill = frame + old due)
    if calc["oldDueAmount"] > 0:
        for mid, paid_now in (calc.get("oldDuePaid") or {}).items():
            if paid_now > 0:
                await db.members.update_one({"id": mid, "clubId": club["id"]},
                                            {"$inc": {"dueAmount": money(-paid_now)}})
        await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}},
                                     {"$set": {"dueAmount": 0}})
    # winner bonus -> winners' wallets
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

    frame_id = uid("f")

    # --- payment log (only the now-collected portion; advance was logged) ---
    if calc["collectedNow"] > 0:
        await payment_log(club["id"], "frames", calc["collectedNow"], payload.mode,
                          f"Frame · {s.get('tableName', '')} · ₹{fmt(calc['collectedNow'])}",
                          actor=user.get("name", ""), ref_type="frame",
                          ref_id=frame_id)
    # --- due-harvest ledger entry (sirf ab aaya cash; advance part already booked)
    if calc["oldDueNow"] > 0:
        names = ", ".join(
            f"{members_map[mid]['name']} ₹{fmt(amt)}"
            for mid, amt in (calc.get("oldDuePaid") or {}).items()
            if mid in members_map and amt > 0)
        await payment_log(club["id"], "due", calc["oldDueNow"], payload.mode,
                          f"Due collected with frame · {s.get('tableName', '')} · {names}",
                          actor=user.get("name", ""), ref_type="frame",
                          ref_id=frame_id)

    # --- frame record ------------------------------------------------------
    players_out = []
    win_pids = {w["pid"] for w in winners}
    for p in s["players"]:
        p2 = dict(p)
        p2["isWinner"] = p2["pid"] in win_pids
        players_out.append(p2)
    frame = {
        "id": frame_id, "clubId": club["id"], "tableId": s["tableId"],
        "tableName": s.get("tableName", ""), "sessionId": s["id"],
        "startedAt": s["startedAt"], "endedAt": s["endedAt"],
        "durationMinutes": calc["minutes"], "players": players_out,
        "winners": [w["label"] for w in winners],
        "winnersPids": [w["pid"] for w in winners],
        "losers": [l["label"] for l in losers],
        "matchMode": s.get("matchMode", "solo"),
        "hourlyRate": calc["hourlyRate"], "minCharge": calc["minCharge"],
        "peak": s.get("peak", False),
        "tableAmount": calc["tableAmount"], "itemsAmount": calc["itemsAmount"],
        "items": calc["items"],
        "membershipDiscount": calc["membershipDiscount"],
        "winnerBonus": calc["winnerBonus"], "discount": calc["discount"],
        "gloves": calc["gloves"], "gloveCharges": calc["gloveCharges"],
        "frameAmount": calc["frameAmount"],
        "advancePaid": s.get("advancePaid", 0), "advanceUsed": calc["advanceUsed"],
        "cashCollected": calc["collectedNow"],
        "settlements": calc["settlements"], "passApplied": calc["passApplied"],
        "winnerCredits": winner_credits,
        "notes": s.get("notes", ""),
        "createdAt": now_iso(), "billedBy": user.get("name", ""),
    }
    # web FrameRecord ka poora alias set — write-time (legacy ke liye read-time
    # backfill services.frame_web_aliases karta hai)
    frame.update(calc_web_aliases(calc, winners, losers,
                                  mode=payload.mode,
                                  requested=payload.paymentMode or payload.mode))
    await db.frames.insert_one(frame)
    frame.pop("_id", None)
    await write_log(club["id"], "BILLING",
                    f"Frame billed · {s.get('tableName', '')} · ₹{fmt(calc['frameAmount'])}"
                    f" · winners: {', '.join(frame['winners'])}",
                    actor=user.get("name", ""), ref_type="frame", ref_id=frame["id"])

    # --- session dies on confirm; the frame is the record ------------------
    await db.sessions.delete_one({"id": session_id})
    message = f"Session confirmed · ₹{fmt(calc['frameAmount'])}"
    if calc["notes"]:
        message += " · " + "; ".join(calc["notes"])
    if wants_web(request):
        return frame  # web confirm response ko BARE FrameRecord ki tarah use karti hai
    return {"frame": frame, "message": message}


@router.delete("/{session_id}")
async def cancel_session(club_id: str, session_id: str, user: dict = Depends(current_user)):
    """Cancel = delete + restore attached stock. Gloves never charged (no frame)."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    s = await _get_session(db, club["id"], session_id)
    if s.get("billingLock"):
        raise HTTPException(400, "Bill is being processed — resume or confirm instead")
    for item in s.get("items") or []:
        await db.menu_items.update_one(
            {"id": item["menuItemId"], "clubId": club["id"]},
            {"$inc": {"stockQty": item["qty"]}})
    await db.sessions.delete_one({"id": session_id})
    msg = f"Session cancelled · {s.get('tableName', '')}"
    if s.get("items"):
        msg += f" · {sum(i['qty'] for i in s['items'])} item(s) returned to stock"
    if (s.get("advancePaid") or 0) > 0:
        msg += f" · advance ₹{fmt(s['advancePaid'])} was collected — refund it manually"
    await write_log(club["id"], "WARNING", msg, actor=user.get("name", ""))
    return {"ok": True, "message": msg}
