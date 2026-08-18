"""Menu items (stock ledger), restock with weighted cost + auto expense,
counter item bills (cash/UPI/card/wallet/due/mixed), paid-marking, deletions
as reversal + stock restore."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..models import (ItemBillIn, ItemBillPatchIn, MarkPaidIn, MenuItemIn,
                      MenuItemPatchIn, RestockIn)
from ..services import (bill_web_aliases, billing_gate, get_club, payment_log,
                        write_log)
from ..util import fmt, money, now_iso, today_ist, uid

router = APIRouter(prefix="/clubs/{club_id}", tags=["items"])


# --------------------------------------------------------------- menu items
@router.get("/menu-items")
async def list_items(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    items = await db.menu_items.find({"clubId": club["id"]}).to_list(None)
    items.sort(key=lambda i: (i.get("category", ""), i.get("name", "")))
    return items


@router.post("/menu-items", status_code=201)
async def create_item(club_id: str, payload: MenuItemIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    item = {
        "id": uid("it"), "clubId": club["id"], "name": payload.name.strip(),
        "category": payload.category.strip() or "Cafe", "price": money(payload.price),
        "costPrice": money(payload.costPrice), "stockQty": payload.stockQty,
        "unit": payload.unit.strip() or "pc", "reorderLevel": payload.reorderLevel,
        "active": True, "createdAt": now_iso(),
    }
    await db.menu_items.insert_one(item)
    item.pop("_id", None)
    return item


@router.patch("/menu-items/{item_id}")
async def patch_item(club_id: str, item_id: str, payload: MenuItemPatchIn,
                     user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    ops = {k: v for k, v in payload.model_dump().items() if v is not None}
    for key in ("price", "costPrice"):
        if key in ops:
            ops[key] = money(ops[key])
    if not ops:
        raise HTTPException(400, "Nothing to update")
    item = await db.menu_items.find_one_and_update(
        {"id": item_id, "clubId": club["id"]}, {"$set": ops})
    if not item:
        raise HTTPException(404, "Item not found")
    return item


@router.delete("/menu-items/{item_id}")
async def delete_item(club_id: str, item_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    res = await db.menu_items.delete_one({"id": item_id, "clubId": club["id"]})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(404, "Item not found")
    return {"ok": True, "message": "Item deleted"}


@router.post("/menu-items/{item_id}/restock")
async def restock_item(club_id: str, item_id: str, payload: RestockIn,
                       user: dict = Depends(current_user)):
    """stock += qty, weighted-average cost, and an automatic stock-category expense."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    item = await db.menu_items.find_one({"id": item_id, "clubId": club["id"]})
    if not item:
        raise HTTPException(404, "Item not found")
    old_qty = int(item.get("stockQty", 0))
    old_cost = float(item.get("costPrice") or 0)
    new_qty = old_qty + payload.qty
    weighted = money((old_qty * old_cost + payload.qty * payload.unitCost) / new_qty) \
        if new_qty else money(payload.unitCost)
    item = await db.menu_items.find_one_and_update(
        {"id": item_id},
        {"$set": {"stockQty": new_qty, "costPrice": weighted}})
    expense_amount = money(payload.qty * payload.unitCost)
    if expense_amount > 0:
        expense = {
            "id": uid("e"), "clubId": club["id"],
            "title": f"Stock purchase · {item['name']} ×{payload.qty}",
            "category": "stock", "amount": expense_amount, "date": today_ist(),
            "note": f"Auto from restock @ ₹{fmt(payload.unitCost)}/{item.get('unit', 'pc')}",
            "createdAt": now_iso(), "auto": True,
            "refType": "menu_item", "refId": item["id"],  # web auto-stock badge
        }
        await db.expenses.insert_one(expense)
    await write_log(club["id"], "BILLING",
                    f"Restocked {item['name']} +{payload.qty} · expense ₹{fmt(expense_amount)}",
                    actor=user.get("name", ""))
    return {"item": item,
            "message": f"Restocked {item['name']} +{payload.qty} · expense ₹{fmt(expense_amount)}"}


# --------------------------------------------------------------- item bills
@router.get("/item-bills")
async def list_bills(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    bills = await db.item_bills.find({"clubId": club["id"]},
                                     sort=[("createdAt", -1)]).to_list(300)
    for b in bills:
        bill_web_aliases(b)  # legacy bills ko web shape complete kar do
    return bills


@router.post("/item-bills", status_code=201)
async def create_bill(club_id: str, payload: ItemBillIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()

    member = None
    if payload.memberId:
        member = await db.members.find_one({"id": payload.memberId, "clubId": club["id"]})
        if not member:
            raise HTTPException(404, "Member not found")
    customer = (payload.customerName or "").strip() or (member["name"] if member else "")
    if not customer:
        raise HTTPException(400, "Customer name is required — or pick a member")

    # --- atomic stock decrement per line (oversell -> 400 readable) --------
    lines, decremented = [], []
    for line in payload.items:
        stock = await db.menu_items.find_one_and_update(
            {"id": line.menuItemId, "clubId": club["id"], "active": True,
             "stockQty": {"$gte": line.qty}},
            {"$inc": {"stockQty": -line.qty}}, after=False)
        if not stock:
            for done in decremented:  # roll back earlier lines
                await db.menu_items.update_one({"id": done["menuItemId"]},
                                               {"$inc": {"stockQty": done["qty"]}})
            item = await db.menu_items.find_one({"id": line.menuItemId,
                                                 "clubId": club["id"]})
            if not item or not item.get("active", True):
                raise HTTPException(404, "Item not found (or inactive)")
            raise HTTPException(400, f"Only {item.get('stockQty', 0)} {item['name']} left in stock")
        decremented.append({"menuItemId": line.menuItemId, "qty": line.qty})
        price = float(stock["price"])
        cost = float(stock.get("costPrice") or 0)
        lines.append({"menuItemId": stock["id"], "name": stock["name"], "price": price,
                      "qty": line.qty, "amount": money(price * line.qty),
                      "costPrice": cost, "costAmount": money(cost * line.qty)})

    subtotal = money(sum(li["amount"] for li in lines))
    if payload.discount and payload.discount > subtotal:
        for done in decremented:
            await db.menu_items.update_one({"id": done["menuItemId"]},
                                           {"$inc": {"stockQty": done["qty"]}})
        raise HTTPException(400, f"Discount can't exceed the bill (₹{fmt(subtotal)})")
    total = money(subtotal - (payload.discount or 0))
    cost_total = money(sum(li["costAmount"] for li in lines))

    mode = payload.mode
    payments = []
    if mode == "mixed":
        payments = [{"mode": p.mode, "amount": money(p.amount)} for p in payload.payments]
        if money(sum(p["amount"] for p in payments)) != total:
            for done in decremented:
                await db.menu_items.update_one({"id": done["menuItemId"]},
                                               {"$inc": {"stockQty": done["qty"]}})
            raise HTTPException(400, f"Mixed payments must add up to ₹{fmt(total)}")
    elif mode == "wallet":
        if not member:
            for done in decremented:
                await db.menu_items.update_one({"id": done["menuItemId"]},
                                               {"$inc": {"stockQty": done["qty"]}})
            raise HTTPException(400, "Wallet payment needs a member")
        if money(member.get("walletBalance") or 0) < total:
            for done in decremented:
                await db.menu_items.update_one({"id": done["menuItemId"]},
                                               {"$inc": {"stockQty": done["qty"]}})
            raise HTTPException(
                400, f"Wallet balance is short — ₹{fmt(member.get('walletBalance') or 0)} available")
        await db.members.update_one({"id": member["id"]},
                                    {"$inc": {"walletBalance": -total}})
        payments = [{"mode": "wallet", "amount": total}]
    elif mode == "due":
        if not member:
            for done in decremented:
                await db.menu_items.update_one({"id": done["menuItemId"]},
                                               {"$inc": {"stockQty": done["qty"]}})
            raise HTTPException(400, "Due payment needs a member")
        await db.members.update_one({"id": member["id"]},
                                    {"$inc": {"dueAmount": total}})
        payments = []
    else:
        payments = [{"mode": mode, "amount": total}]

    bill = {
        "id": uid("bill"), "clubId": club["id"], "items": lines,
        "customerName": customer, "memberId": member["id"] if member else None,
        "memberName": member["name"] if member else None,
        "mode": ("mixed" if mode == "mixed" else mode),
        "paymentMode": ("mixed" if mode == "mixed" else mode),  # web alias
        "payments": payments,
        "subtotal": subtotal, "discount": money(payload.discount or 0),
        "amount": total, "total": total,  # total = web alias
        "costAmount": cost_total,
        "profit": money(total - cost_total),
        "paidAmount": 0.0 if mode == "due" else total,  # web alias (ItemBills/Search)
        "dueAmount": total if mode == "due" else 0.0,   # web alias
        "paid": mode != "due", "status": "paid" if mode != "due" else "unpaid",
        "createdAt": now_iso(), "paidAt": now_iso() if mode != "due" else None,
        "updatedAt": now_iso(),
        "createdBy": user.get("name", ""),
    }
    await db.item_bills.insert_one(bill)
    bill.pop("_id", None)

    for p in payments:
        await payment_log(club["id"], "items", p["amount"], p["mode"],
                          f"Item bill · {customer} · ₹{fmt(p['amount'])} ({p['mode']})",
                          actor=user.get("name", ""), member_id=bill["memberId"],
                          ref_type="item_bill", ref_id=bill["id"])
    if mode == "due":
        await write_log(club["id"], "WARNING",
                        f"Item bill on due · {customer} · ₹{fmt(total)}",
                        actor=user.get("name", ""), member_id=bill["memberId"],
                        ref_type="item_bill", ref_id=bill["id"], amount=total)
    parts = ", ".join(f"{li['name']}×{li['qty']}" for li in lines)
    return {"bill": bill, "message": f"Bill saved · ₹{fmt(total)} ({parts})"}


@router.patch("/item-bills/{bill_id}")
async def patch_bill(club_id: str, bill_id: str, payload: ItemBillPatchIn,
                     user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    if not payload.customerName:
        raise HTTPException(400, "Nothing to update")
    bill = await db.item_bills.find_one_and_update(
        {"id": bill_id, "clubId": club["id"]},
        {"$set": {"customerName": payload.customerName.strip(),
                  "updatedAt": now_iso()}})
    if not bill:
        raise HTTPException(404, "Bill not found")
    return bill_web_aliases(bill)


@router.post("/item-bills/{bill_id}/mark-paid")
async def mark_paid(club_id: str, bill_id: str, payload: MarkPaidIn,
                    user: dict = Depends(current_user)):
    """Due bill ka paisa collect — FULL ya PARTIAL (web MarkPaidModal partial
    bhejta hai: {amount, mode}). Payment received month ko credit hota hai aur
    member ka due sirf utne se ghtta hai; outstanding 0 pe bill paid."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    bill = await db.item_bills.find_one({"id": bill_id, "clubId": club["id"]})
    if not bill:
        raise HTTPException(404, "Bill not found")
    bill_web_aliases(bill)  # legacy bills: outstanding derive
    outstanding = money(bill["dueAmount"])
    if bill.get("paid") or outstanding <= 0:
        raise HTTPException(400, "Bill is already paid")
    amt = money(payload.amount) if payload.amount else outstanding
    if amt > outstanding:
        raise HTTPException(400, f"Payment exceeds the bill due (₹{fmt(outstanding)})")
    new_paid = money(bill["paidAmount"] + amt)
    new_due = money(outstanding - amt)
    settled = new_due <= 0
    bill = await db.item_bills.find_one_and_update(
        {"id": bill_id},
        {"$set": {"paidAmount": new_paid, "dueAmount": new_due,
                  "paid": settled, "status": "paid" if settled else "partial",
                  "updatedAt": now_iso(),
                  **({"paidAt": now_iso()} if settled else {}),
                  "settledMode": payload.mode}})
    if bill.get("memberId"):
        await db.members.update_one({"id": bill["memberId"]},
                                    {"$inc": {"dueAmount": -amt},
                                     "$set": {"updatedAt": now_iso()}})
        await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}},
                                     {"$set": {"dueAmount": 0}})
    await payment_log(club["id"], "items", amt, payload.mode,
                      f"Item bill settled · {bill['customerName']} · ₹{fmt(amt)}" +
                      ("" if settled else f" · ₹{fmt(new_due)} left"),
                      actor=user.get("name", ""), member_id=bill.get("memberId"),
                      ref_type="item_bill", ref_id=bill["id"])
    msg = f"Bill paid · {bill['customerName']} · ₹{fmt(amt)}"
    if not settled:
        msg += f" · ₹{fmt(new_due)} left"
    return {"bill": bill_web_aliases(bill), "message": msg}


@router.delete("/item-bills/{bill_id}")
async def delete_bill(club_id: str, bill_id: str, user: dict = Depends(current_user)):
    """Delete = reversal: stock restore + member wallet/due rollback.
    (Payment ledger entries are history and stay, per product behavior.)"""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    bill = await db.item_bills.find_one({"id": bill_id, "clubId": club["id"]})
    if not bill:
        raise HTTPException(404, "Bill not found")
    bill_web_aliases(bill)  # outstanding due / wallet-paid part derive
    for li in bill.get("items") or []:
        await db.menu_items.update_one({"id": li["menuItemId"], "clubId": club["id"]},
                                       {"$inc": {"stockQty": li["qty"]}})
    if bill.get("memberId"):
        if bill.get("mode") == "wallet" and money(bill.get("paidAmount") or 0) > 0:
            await db.members.update_one({"id": bill["memberId"]},
                                        {"$inc": {"walletBalance": money(bill["paidAmount"])},
                                         "$set": {"updatedAt": now_iso()}})
        if money(bill["dueAmount"]) > 0:  # sirf OUTSTANDING due wapas ghatao
            await db.members.update_one({"id": bill["memberId"]},
                                        {"$inc": {"dueAmount": -money(bill["dueAmount"])},
                                         "$set": {"updatedAt": now_iso()}})
            await db.members.update_many({"clubId": club["id"], "dueAmount": {"$lt": 0}},
                                         {"$set": {"dueAmount": 0}})
    await db.item_bills.delete_one({"id": bill_id})
    await write_log(club["id"], "ADMIN",
                    f"Item bill deleted · {bill['customerName']} · ₹{fmt(bill['amount'])} · stock restored",
                    actor=user.get("name", ""), ref_type="item_bill", ref_id=bill_id)
    return {"ok": True, "message": f"Bill deleted · {bill['customerName']} · stock restored"}
