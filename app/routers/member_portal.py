"""Member-only dashboard, history, rankings and food ordering endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..models import In
from ..services import frame_web_aliases, public_member
from ..util import money, now_iso, uid

router = APIRouter(prefix="/member-portal", tags=["member-portal"])


class OrderLineIn(In):
    menuItemId: str
    quantity: int


class OrderIn(In):
    items: list[OrderLineIn]
    mode: str = "wallet"


async def _member_context(user: dict):
    member_id = user.get("memberId") or (user.get("id") if user.get("role") == "member" else None)
    club_id = user.get("clubId") or ((user.get("clubIds") or [None])[0])
    if not member_id or not club_id:
        raise HTTPException(403, "This account is not linked to a club member profile")
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "clubId": club_id, "active": {"$ne": False}})
    club = await db.clubs.find_one({"id": club_id})
    if not member or not club:
        raise HTTPException(403, "This account is not linked to an active club membership")
    return db, member, club


def _member_frame(frame: dict, member_id: str) -> dict | None:
    frame = frame_web_aliases(frame)
    settlements = [s for s in frame.get("settlements", []) if s.get("memberId") == member_id]
    if not settlements:
        return None
    due = money(sum(money(s.get("duePart") or 0) for s in settlements))
    if due <= 0:
        return None
    result = dict(frame)
    result["memberSettlement"] = settlements[0]
    result["memberDue"] = due
    result["isDue"] = True
    result.pop("_id", None)
    return result


async def _portal_data(user: dict):
    db, member, club = await _member_context(user)
    cid, mid = club["id"], member["id"]
    tables = await db.tables.find({"clubId": cid, "active": {"$ne": False}}).to_list(None)
    sessions = await db.sessions.find({"clubId": cid, "status": {"$in": ["running", "live"]}}).to_list(None)
    live_by_table = {s.get("tableId"): s for s in sessions}
    for table in tables:
        session = live_by_table.get(table.get("id"))
        table["status"] = "live" if session else "free"
        if session:
            table["sessionId"] = session.get("id")
            table["startedAt"] = session.get("startedAt")
            table["players"] = [p.get("label", "") for p in session.get("players", [])]
        table.pop("_id", None)
    frames = await db.frames.find({"clubId": cid}, sort=[("createdAt", -1)]).to_list(500)
    due_frames = [item for frame in frames if (item := _member_frame(frame, mid))]
    menu = await db.menu_items.find({"clubId": cid, "active": {"$ne": False}, "stockQty": {"$gt": 0}}).to_list(None)
    for item in menu:
        item.pop("_id", None)
    logs = await db.logs.find({"clubId": cid, "memberId": mid}, sort=[("createdAt", -1)]).to_list(200)
    orders = await db.item_bills.find({"clubId": cid, "memberId": mid, "source": "member_order"}, sort=[("createdAt", -1)]).to_list(100)
    for doc in logs + orders:
        doc.pop("_id", None)
    return db, member, club, tables, due_frames, menu, logs, orders


@router.get("/dashboard")
async def dashboard(user: dict = Depends(current_user)):
    _, member, club, tables, due_frames, menu, logs, orders = await _portal_data(user)
    return {
        "member": public_member(member),
        "club": {k: v for k, v in club.items() if k != "_id"},
        "tables": tables,
        "dueFrames": due_frames,
        "menuItems": menu,
        "activity": logs,
        "orders": orders,
        "serverNow": now_iso(),
    }


@router.get("/rankings")
async def rankings(user: dict = Depends(current_user)):
    db, member, club = await _member_context(user)
    members = await db.members.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    frames = await db.frames.find({"clubId": club["id"]}).to_list(None)
    stats = {m["id"]: {"memberId": m["id"], "name": m.get("name", "Member"), "won": 0, "lost": 0, "frames": 0} for m in members}
    for frame in frames:
        winners = set(frame.get("winnersPids") or frame.get("winnerPlayerIds") or [])
        for player in frame.get("players", []):
            mid = player.get("memberId")
            if mid not in stats:
                continue
            stats[mid]["frames"] += 1
            if player.get("pid") in winners or player.get("id") in winners:
                stats[mid]["won"] += 1
            else:
                stats[mid]["lost"] += 1
    rows = list(stats.values())
    rows.sort(key=lambda row: (-row["won"], row["lost"], -row["frames"], row["name"].lower()))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
        row["isMe"] = row["memberId"] == member["id"]
    mine = next(row for row in rows if row["isMe"])
    return {"rankings": rows, "me": mine}


@router.get("/orders")
async def orders(user: dict = Depends(current_user)):
    _, _, _, _, _, _, _, saved = await _portal_data(user)
    return {"orders": saved}


@router.post("/orders", status_code=201)
async def create_order(payload: OrderIn, user: dict = Depends(current_user)):
    if payload.mode != "wallet":
        raise HTTPException(400, "Online orders currently support secure wallet payment only")
    if not payload.items:
        raise HTTPException(400, "Add at least one item to the order")
    db, member, club = await _member_context(user)
    lines, total = [], 0.0
    for requested in payload.items:
        if requested.quantity < 1 or requested.quantity > 50:
            raise HTTPException(400, "Each item quantity must be between 1 and 50")
        item = await db.menu_items.find_one_and_update(
            {"id": requested.menuItemId, "clubId": club["id"], "active": True, "stockQty": {"$gte": requested.quantity}},
            {"$inc": {"stockQty": -requested.quantity}}, after=False)
        if not item:
            for line in lines:
                await db.menu_items.update_one({"id": line["menuItemId"]}, {"$inc": {"stockQty": line["qty"]}})
            raise HTTPException(400, "One item is unavailable or out of stock")
        amount = money(float(item["price"]) * requested.quantity)
        total = money(total + amount)
        lines.append({"menuItemId": item["id"], "name": item["name"], "price": item["price"], "qty": requested.quantity, "amount": amount})
    if money(member.get("walletBalance") or 0) < total:
        for line in lines:
            await db.menu_items.update_one({"id": line["menuItemId"]}, {"$inc": {"stockQty": line["qty"]}})
        raise HTTPException(400, "Wallet balance is not enough for this order")
    await db.members.update_one({"id": member["id"]}, {"$inc": {"walletBalance": -total}, "$set": {"updatedAt": now_iso()}})
    order = {
        "id": uid("order"), "clubId": club["id"], "memberId": member["id"], "memberName": member.get("name"),
        "source": "member_order", "items": lines, "amount": total, "total": total,
        "mode": "wallet", "paymentMode": "wallet", "paid": True, "status": "paid",
        "createdAt": now_iso(), "paidAt": now_iso(), "customerName": member.get("name"),
    }
    await db.item_bills.insert_one(order)
    order.pop("_id", None)
    return {"order": order, "newWalletBalance": money((member.get("walletBalance") or 0) - total), "message": "Order placed and paid from wallet"}