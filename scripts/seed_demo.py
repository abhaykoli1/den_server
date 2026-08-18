"""Demo seed — run ONCE on a clean DB (NOT idempotent).

    # wipe snapshot first if dev mode was used before:
    rm -f backend/.devdata/snapshot.json
    python3 scripts/seed_demo.py

Creates: demo logins (owner@rowdys.dev · master@rowdys.dev · staff2@rowdys.dev),
club "Rowdy's Den", 2 tables (Snooker ₹240/hr min₹20 · Pool ₹180/hr min₹15),
stock items + restock expenses, members (Ravi wallet / Imran due / Arjun pass /
Sana monthly), an item bill, expenses — plus a COMPLETED "Friday Snooker Open"
and a RUNNING "Sunday Pool Challenge".
"""
import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_db  # noqa: E402
from app.tournament import build_knockout, _advance  # noqa: E402
from app.util import iso, money, now_iso, now_utc, today_ist, uid  # noqa: E402

OWNER, MASTER, STAFF = "owner@rowdys.dev", "master@rowdys.dev", "staff2@rowdys.dev"


async def main():
    db = await get_db()
    now = now_iso()
    today = today_ist()
    if await db.clubs.count_documents({"name": "Rowdy's Den"}):
        print("!! Looks seeded already. Wipe .devdata/snapshot.json or use a fresh DB.")
        return

    # ------------------------------------------------------------ users+plans
    owner = {"id": uid("u"), "email": OWNER, "name": "Raju Bhai", "picture": "",
             "phone": "9829012345", "location": "Jaipur", "role": "owner",
             "active": True, "clubIds": [], "createdAt": now, "updatedAt": now,
             "lastLoginAt": now,
             "subscription": {"planId": "sp_pro", "planName": "Pro Monthly",
                              "status": "active", "price": 999,
                              "billingCycle": "monthly", "durationDays": 30,
                              "maxClubs": 3, "selectedAt": now, "startsAt": now,
                              "expiresAt": iso(now_utc() + timedelta(days=40)),
                              "notes": "seeded", "updatedAt": now}}
    master = {"id": uid("u"), "email": MASTER, "name": "Master Boss", "picture": "",
              "phone": "", "location": "", "role": "master", "active": True,
              "clubIds": [], "subscription": None, "createdAt": now,
              "updatedAt": now, "lastLoginAt": now}
    club_id = "club_rowdys_den"
    owner["clubIds"] = [club_id]
    staff = {"id": uid("u"), "email": STAFF, "name": "Abhay Koli", "picture": "",
             "phone": "9988776655", "location": "Jaipur", "role": "staff",
             "active": True, "clubIds": [club_id], "subscription": None,
             "createdAt": now, "updatedAt": now, "lastLoginAt": now}
    for u in (owner, master, staff):
        await db.users.insert_one(u)

    plans = [
        {"id": "sp_starter", "name": "Starter", "description": "One club, core billing",
         "price": 499, "billingCycle": "monthly", "durationDays": 30, "trialDays": 7,
         "maxClubs": 1, "features": ["Live table billing", "Players & dues",
                                     "Item counter"],
         "active": True, "recommended": False, "sortOrder": 1, "createdAt": now},
        {"id": "sp_pro", "name": "Pro Monthly", "description": "Full club + reports",
         "price": 999, "billingCycle": "monthly", "durationDays": 30, "trialDays": 14,
         "maxClubs": 3, "features": ["Everything in Starter", "Finance & reports",
                                     "Tournaments", "Email alerts"],
         "active": True, "recommended": True, "sortOrder": 2, "createdAt": now},
        {"id": "sp_annual", "name": "Pro Annual", "description": "Best value, 2 months free",
         "price": 9999, "billingCycle": "yearly", "durationDays": 365, "trialDays": 0,
         "maxClubs": 5, "features": ["Everything in Pro", "Priority support"],
         "active": True, "recommended": False, "sortOrder": 3, "createdAt": now},
    ]
    for p in plans:
        await db.subscription_plans.insert_one(p)

    # ---------------------------------------------------------------- club
    club = {"id": club_id, "name": "Rowdy's Den", "logo": "", "ownerUserId": owner["id"],
            "settings": {"winnerBonus": 30, "dueLimit": 1000, "defaultAdvance": 100,
                         "currency": "INR", "currencySymbol": "₹",
                         "monthlyTableDiscount": 10},
            "createdAt": now}
    await db.clubs.insert_one(club)

    tables = [
        {"id": uid("t"), "clubId": club_id, "name": "Snooker 1", "active": True,
         "sortOrder": 1, "rate": {"hourlyRate": 240, "minCharge": 20,
                                  "ratesByPlayers": {"4": 300},
                                  "peakHourlyRate": 300, "peakStartHour": 18,
                                  "peakEndHour": 23, "glovePrice": 30}},
        {"id": uid("t"), "clubId": club_id, "name": "Pool 1", "active": True,
         "sortOrder": 2, "rate": {"hourlyRate": 180, "minCharge": 15,
                                  "ratesByPlayers": {}, "glovePrice": 0}},
        {"id": uid("t"), "clubId": club_id, "name": "Pool 2", "active": True,
         "sortOrder": 3, "rate": {"hourlyRate": 180, "minCharge": 15,
                                  "ratesByPlayers": {}, "glovePrice": 0}},
    ]
    for t in tables:
        await db.tables.insert_one(t)

    # --------------------------------------------------------------- members
    members = [
        {"name": "Ravi Kumar", "phone": "9820000001", "email": "ravi@example.com",
         "walletBalance": 500.0, "dueAmount": 0.0, "passFramesLeft": 0,
         "planName": "Gold Wallet", "planType": "wallet"},
        {"name": "Imran Khan", "phone": "9820000002", "email": "",
         "walletBalance": 0.0, "dueAmount": 120.0, "passFramesLeft": 0,
         "planName": None, "planType": None},
        {"name": "Arjun Singh", "phone": "9820000003", "email": "arjun@example.com",
         "walletBalance": 40.0, "dueAmount": 0.0, "passFramesLeft": 7,
         "planName": "Frame Pass", "planType": "pass"},
        {"name": "Sana Qureshi", "phone": "9820000004", "email": "",
         "walletBalance": 0.0, "dueAmount": 250.0, "passFramesLeft": 0,
         "planName": "Monthly", "planType": "monthly", "tableDiscountPercent": 10},
        {"name": "Dev Patel", "phone": "9820000005", "email": "",
         "walletBalance": 0.0, "dueAmount": 0.0, "passFramesLeft": 0,
         "planName": None, "planType": None},
    ]
    member_docs = {}
    for m in members:
        doc = {"id": uid("m"), "clubId": club_id, "type": "regular", "active": True,
               "notes": "", "createdAt": now, "planId": None, "planExpiresAt": None,
               "tableDiscountPercent": m.get("tableDiscountPercent"),
               "email": m.get("email", ""), **{k: m[k] for k in
               ("name", "phone", "walletBalance", "dueAmount", "passFramesLeft",
                "planName", "planType")}}
        member_docs[m["name"]] = doc
        await db.members.insert_one(doc)

    club_plans = [
        {"name": "Gold Wallet", "type": "wallet", "amount": 1000, "value": 1100,
         "frames": 0, "days": 0, "tableDiscountPercent": None, "isDefault": True,
         "description": "Prepaid wallet — ₹100 bonus"},
        {"name": "Frame Pass", "type": "pass", "amount": 900, "value": 0,
         "frames": 10, "days": 30, "tableDiscountPercent": None, "isDefault": False,
         "description": "10 frames, 30 days"},
        {"name": "Monthly", "type": "monthly", "amount": 1500, "value": 0,
         "frames": 0, "days": 30, "tableDiscountPercent": 10, "isDefault": False,
         "description": "10% off table money, monthly"},
    ]
    for p in club_plans:
        await db.plans.insert_one({"id": uid("plan"), "clubId": club_id,
                                   "active": True, "createdAt": now, **p})

    # -------------------------------------------------------- stock + bills
    menu = [
        ("Coca Cola", "Cafe", 40, 28, 24), ("Samosa", "Cafe", 25, 12, 30),
        ("Cold Coffee", "Cafe", 90, 45, 12), ("Chai", "Cafe", 20, 8, 40),
        ("Chips", "Snacks", 30, 20, 4), ("Water Bottle", "Cafe", 20, 12, 18),
    ]
    item_docs = []
    for name, cat, price, cost, qty in menu:
        it = {"id": uid("it"), "clubId": club_id, "name": name, "category": cat,
              "price": price, "costPrice": cost, "stockQty": qty, "unit": "pc",
              "reorderLevel": 5, "active": True, "createdAt": now}
        item_docs.append(it)
        await db.menu_items.insert_one(it)
        await db.expenses.insert_one({
            "id": uid("e"), "clubId": club_id, "title": f"Stock purchase · {name} ×{qty}",
            "category": "stock", "amount": money(qty * cost), "date": today,
            "note": "Seeded opening stock", "createdAt": now, "auto": True})
    samosa = item_docs[1]
    cola = item_docs[0]

    def payment(source, amount, mode, msg, member_id=None):
        return {"id": uid("log"), "clubId": club_id, "tag": "PAYMENT", "type": source,
                "amount": money(amount), "mode": mode, "message": msg,
                "actorName": "Raju Bhai", "memberId": member_id, "createdAt": now}

    bills = [
        {"customerName": "Walk in", "memberId": None, "mode": "cash", "paid": True,
         "status": "paid", "items": [{"menuItemId": samosa["id"], "name": "Samosa",
         "price": 25, "qty": 2, "amount": 50, "costPrice": 12, "costAmount": 24}],
         "amount": 50, "costAmount": 24, "profit": 26, "discount": 0},
        {"customerName": "Imran Khan", "memberId": member_docs["Imran Khan"]["id"],
         "mode": "due", "paid": False, "status": "unpaid",
         "items": [{"menuItemId": cola["id"], "name": "Coca Cola", "price": 40,
                    "qty": 3, "amount": 120, "costPrice": 28, "costAmount": 84}],
         "amount": 120, "costAmount": 84, "profit": 36, "discount": 0},
    ]
    for b in bills:
        doc = {"id": uid("bill"), "clubId": club_id, "paidAt": now if b["paid"] else None,
               "createdAt": now, "createdBy": "Raju Bhai", "payments":
               [{"mode": b["mode"], "amount": b["amount"]}] if b["paid"] else [],
               "memberName": b["customerName"],
               "subtotal": b["amount"] + b["discount"],
               **{k: b[k] for k in ("customerName", "memberId", "mode", "paid", "status",
                                    "items", "amount", "costAmount", "profit",
                                    "discount")}}
        cola["stockQty"] -= 3 if not b["paid"] else 0
        samosa["stockQty"] -= 2 if b["paid"] else 0
        await db.item_bills.insert_one(doc)
        if b["paid"]:
            await db.logs.insert_one(payment("items", b["amount"], b["mode"],
                                             f"Item bill · {b['customerName']} · ₹{b['amount']} (cash)",
                                             b["memberId"]))
    await db.menu_items.update_one({"id": cola["id"]}, {"$set": {"stockQty": cola["stockQty"]}})
    await db.menu_items.update_one({"id": samosa["id"]}, {"$set": {"stockQty": samosa["stockQty"]}})

    # -------------------------------------------------------- membership sale
    await db.membership_sales.insert_one({
        "id": uid("sale"), "clubId": club_id, "memberId": member_docs["Ravi Kumar"]["id"],
        "memberName": "Ravi Kumar", "planName": "Gold Wallet", "planType": "wallet",
        "amount": 1000, "mode": "upi", "createdAt": now, "planId": None})
    await db.logs.insert_one(payment("memberships", 1000, "upi",
                                     "Plan sold · Gold Wallet → Ravi Kumar (wallet +₹1100)",
                                     member_docs["Ravi Kumar"]["id"]))
    await db.logs.insert_one(payment("due", 80, "cash",
                                     "Due collected · Sana Qureshi · ₹80 · ₹250 left",
                                     member_docs["Sana Qureshi"]["id"]))

    # -------------------------------------------------------------- expenses
    for title, cat, amt in (("Monthly rent", "rent", 8000),
                            ("Electricity bill", "electricity", 2300),
                            ("Table re-felting", "maintenance", 1500)):
        await db.expenses.insert_one({
            "id": uid("e"), "clubId": club_id, "title": title, "category": cat,
            "amount": amt, "date": today, "note": "", "createdAt": now, "auto": False})

    # ------------------------------------------- COMPLETED: Friday Snooker Open
    players = [{"pid": uid("tp"), "name": n, "phone": f"98300000{i:02d}1",
                "memberId": None, "paidEntry": True, "seed": i + 1, "entryFee": 200}
               for i, n in enumerate(("Aarav", "Vihaan", "Kabir", "Zaid"))]
    matches = build_knockout(players)
    # semis -> final: Aarav beats Zaid, Vihaan beats Kabir; Aarav beats Vihaan
    semis = [m for m in matches if m["round"] == 0]
    for m, wname, s in zip(semis, ("Aarav", "Vihaan"), ((3, 1), (3, 2))):
        w = m["p1"] if m["p1"]["name"] == wname else m["p2"]
        l = m["p2"] if w["pid"] == m["p1"]["pid"] else m["p1"]
        m.update({"status": "done", "winnerPid": w["pid"], "loserPid": l["pid"],
                  "score1": s[0], "score2": s[1], "minutes": 48, "tableAmount": 192,
                  "playedAt": now,
                  "startedAt": now, "endedAt": now, "tableName": "Snooker 1"})
        _advance(matches, m, w["pid"])
    final = next(m for m in matches if m["round"] == 1)
    fw = final["p1"] if final["p1"]["name"] == "Aarav" else final["p2"]
    fl = final["p2"] if fw["pid"] == final["p1"]["pid"] else final["p1"]
    final.update({"status": "done", "winnerPid": fw["pid"], "loserPid": fl["pid"],
                  "score1": 4, "score2": 2, "minutes": 65, "tableAmount": 260,
                  "playedAt": now, "startedAt": now, "endedAt": now,
                  "tableName": "Snooker 1"})
    done_tour = {
        "id": uid("tour"), "clubId": club_id, "name": "Friday Snooker Open",
        "game": "Snooker", "date": today, "entryFee": 400, "prize1": 1500,
        "prize2": 500, "maxPlayers": 16, "tableRate": 240, "format": "knockout",
        "notes": "", "status": "completed", "participants": players,
        "matches": matches, "collected": 1600.0, "tableCharges": 644.0,
        "winnerPid": fw["pid"], "winnerName": "Aarav", "runnerUpName": "Vihaan",
        "completedAt": now, "createdAt": now}
    for p in players:
        p["entryFee"] = 400
    await db.tournaments.insert_one(done_tour)
    for p in players:
        await db.logs.insert_one(payment("tournaments", 400, "cash",
                                         f"Tournament entry · {p['name']} · Friday Snooker Open"))
    for e_title, amt in (("Tournament prize · Friday Snooker Open · 1st", 1500),
                         ("Tournament prize · Friday Snooker Open · 2nd", 500)):
        await db.expenses.insert_one({
            "id": uid("e"), "clubId": club_id, "title": e_title,
            "category": "tournament", "amount": amt, "date": today,
            "note": "Auto expense — Friday Snooker Open completed",
            "createdAt": now, "auto": True})

    # --------------------------------------------- RUNNING: Sunday Pool Challenge
    players2 = [{"pid": uid("tp"), "name": n, "phone": f"98300000{i:02d}9",
                 "memberId": None, "paidEntry": i < 3, "seed": i + 1, "entryFee": 150}
                for i, n in enumerate(("Meera", "Dhruv", "Omi", "Rey", "Sana"))]
    matches2 = build_knockout(players2)
    running_tour = {
        "id": uid("tour"), "clubId": club_id, "name": "Sunday Pool Challenge",
        "game": "Pool", "date": today, "entryFee": 150, "prize1": 1000,
        "prize2": 400, "maxPlayers": 16, "tableRate": 180, "format": "knockout",
        "notes": "", "status": "running", "participants": players2,
        "matches": matches2, "collected": 450.0, "tableCharges": 0.0,
        "winnerPid": None, "winnerName": None, "runnerUpName": None,
        "completedAt": None, "createdAt": now}
    await db.tournaments.insert_one(running_tour)

    print("Seeded ✔")
    print("  club      : Rowdy's Den (3 tables)")
    print("  members   : Ravi (wallet ₹500) · Imran (due ₹120) · Arjun (7 frames) · Sana (due ₹250)")
    print("  tournaments: Friday Snooker Open (completed) · Sunday Pool Challenge (running)")
    print("  logins    : owner@rowdys.dev · master@rowdys.dev · staff2@rowdys.dev")


if __name__ == "__main__":
    asyncio.run(main())
