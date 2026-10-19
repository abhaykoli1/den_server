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
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_db  # noqa: E402
from app.tournament import build_knockout, _advance  # noqa: E402
from app.util import IST, iso, money, now_iso, now_utc, parse_iso, today_ist, uid  # noqa: E402

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
         "walletBalance": 0.0, "dueAmount": 170.0, "passFramesLeft": 6,
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

    await seed_members_app(db, club_id, tables, member_docs, item_docs, payment)

    print("Seeded ✔")
    print("  club      : Rowdy's Den (3 tables)")
    print("  members   : Ravi (wallet ₹500) · Imran (due ₹120) · Arjun (due ₹170 · 6 frames) · Sana (due ₹250)")
    print("  tournaments: Friday Snooker Open (completed) · Sunday Pool Challenge (running) · Monthly Masters (upcoming)")
    print("  logins    : owner@rowdys.dev · master@rowdys.dev · staff2@rowdys.dev")
    print("  members app: http://localhost:8000/members  →  ravi@example.com · arjun@example.com")


# =============================================================== members app
DEMO_IMG = "/members/static/members/demo/"


async def seed_members_app(db, club_id, tables, member_docs, item_docs, payment):
    """Everything the members app shows off: pro-shop + cafe catalogue with
    photos, a fortnight of frames for Ravi/Arjun, app orders, bookings, an
    upcoming tournament and a pending UPI request."""
    now = now_iso()
    today = today_ist()
    ravi, arjun = member_docs["Ravi Kumar"], member_docs["Arjun Singh"]
    # UPI details members pay to from the app (deep link on phones, QR on desktop)
    await db.clubs.update_one({"id": club_id}, {"$set": {"upiId": "rowdysden@upi", "phone": "+91 98290 12345",
                                                        "address": "C-Scheme, Jaipur", "hours": "8:00 AM – 11:30 PM"}})
    imran, sana, dev = member_docs["Imran Khan"], member_docs["Sana Qureshi"], member_docs["Dev Patel"]
    snooker, pool1, pool2 = tables[0], tables[1], tables[2]

    def ago(days=0, hours=0, minutes=0):
        return iso(now_utc() - timedelta(days=days, hours=hours, minutes=minutes))

    # ---- photos + kind on the existing cafe items
    photos = {"Cold Coffee": "cold-coffee.jpg"}
    for it in item_docs:
        upd = {"kind": "cafe", "createdAt": ago(days=45)}
        if it["name"] in photos:
            upd["image"] = DEMO_IMG + photos[it["name"]]
        if it["name"] == "Cold Coffee":
            upd["featured"] = True
            upd["description"] = "Slow-brewed, milky and ice-cold. The Den classic between frames."
        await db.menu_items.update_one({"id": it["id"]}, {"$set": upd})

    # ---- more cafe + the pro shop (name, category, kind, price, cost, qty, image, description, featured)
    extra = [
        ("Masala Maggi", "Snacks", "cafe", 60, 25, 30, "", "Two-minute noodles, Den style — extra masala, onions, a squeeze of lime.", False),
        ("Veg Sandwich", "Snacks", "cafe", 70, 30, 14, "", "Grilled, three-layer, mint chutney.", False),
        ("French Fries", "Snacks", "cafe", 80, 30, 20, "", "Crisp salted fries with ketchup.", True),
        ("Red Bull", "Beverages", "cafe", 125, 95, 24, "", "250 ml. For the decider.", False),
        ("Iced Tea", "Beverages", "cafe", 60, 20, 18, "", "Lemon iced tea, fresh brewed.", False),
        ("Brownie", "Desserts", "cafe", 90, 40, 8, "", "Fudgy chocolate brownie, served warm.", False),
        ("Pro Cue · Ash 57\"", "Cues", "shop", 4500, 3200, 3, "cue.jpg", "Hand-spliced ash shaft, rosewood butt, brass joint. 9.5 mm tip. Comes with a soft case.", True),
        ("Cue Tips · Elk Master (5)", "Tips & Ferrules", "shop", 150, 80, 25, "tips.jpg", "Soft leather tips, 10 mm. Ask the counter to fit one for free.", False),
        ("Snooker Chalk · Blue", "Chalk", "shop", 40, 18, 60, "chalk.jpg", "Classic blue cube. Grip on every shot.", False),
        ("Billiard Glove · Black", "Gloves", "shop", 250, 120, 15, "glove.jpg", "Three-finger lycra glove, left hand. One size.", True),
        ("Den Club T-Shirt", "Apparel", "shop", 599, 320, 12, "tshirt.jpg", "Black cotton tee with the 8-ball crest. S / M / L / XL at the counter.", True),
        ("Den Cap", "Apparel", "shop", 349, 180, 10, "cap.jpg", "Black cap, embroidered crest, adjustable strap.", False),
        ("Cue Case · Leather", "Accessories", "shop", 1200, 750, 4, "case.jpg", "Slim 3/4 leather case with shoulder strap.", False),
        ("Chalk Holder · Magnetic", "Accessories", "shop", 120, 60, 20, "", "Clip-on magnetic holder — never lose your chalk again.", False),
        ("Cue Extension · Telescopic", "Accessories", "shop", 450, 260, 6, "", "Screw-on telescopic extension, 25–40 cm.", False),
    ]
    shop_docs = {}
    for i, (name, cat, kind, price, cost, qty, img, desc, feat) in enumerate(extra):
        doc = {"id": uid("it"), "clubId": club_id, "name": name, "category": cat, "kind": kind,
               "price": price, "costPrice": cost, "stockQty": qty, "unit": "pc", "reorderLevel": 3,
               "active": True, "image": (DEMO_IMG + img) if img else "", "description": desc,
               "featured": feat, "createdAt": ago(days=20 - i)}
        shop_docs[name] = doc
        await db.menu_items.insert_one(doc)
        await db.expenses.insert_one({
            "id": uid("e"), "clubId": club_id, "title": f"Stock purchase · {name} ×{qty}",
            "category": "stock", "amount": money(qty * cost), "date": today,
            "note": "Seeded opening stock", "createdAt": now, "auto": True})
    by_name = {**{d["name"]: d for d in item_docs}, **shop_docs}

    # ---- frames (billed games) over the last fortnight
    def player(m, label=None):
        return {"pid": uid("p"), "label": label or m["name"].split()[0], "type": "member", "memberId": m["id"]}

    async def frame(table, started_days, minutes, winner, loser, parts, hour=19, items=None):
        """parts = dict(walletPart/passPart/cashPart/duePart) for the LOSER."""
        wp, lp = player(winner), player(loser)
        rate = table["rate"]["hourlyRate"]
        table_amt = money(rate * minutes / 60)
        items = items or []
        items_amt = money(sum(i["amount"] for i in items))
        bonus = 30.0
        charge = money(table_amt + items_amt + bonus)
        started = iso(now_utc().replace(hour=hour - 5, minute=0, second=0, microsecond=0) - timedelta(days=started_days))
        ended = iso(parse_iso(started) + timedelta(minutes=minutes))
        line = {"pid": lp["pid"], "label": lp["label"], "memberId": loser["id"], "name": lp["label"],
                "playerName": lp["label"], "memberName": loser["name"], "charge": charge,
                "membershipDiscount": 0.0, "walletPart": 0.0, "passPart": 0.0, "cashPart": 0.0,
                "duePart": 0.0, "oldDuePart": 0.0, **parts}
        cash = money(line["cashPart"])
        fid = uid("f")
        doc = {"id": fid, "clubId": club_id, "tableId": table["id"], "tableName": table["name"],
               "sessionId": uid("s"), "startedAt": started, "endedAt": ended, "durationMinutes": minutes,
               "players": [{**wp, "isWinner": True}, {**lp, "isWinner": False}],
               "winners": [wp["label"]], "winnersPids": [wp["pid"]], "losers": [lp["label"]],
               "matchMode": "solo", "hourlyRate": rate, "minCharge": table["rate"].get("minCharge", 0),
               "peak": hour >= 18, "tableAmount": table_amt, "itemsAmount": items_amt, "items": items,
               "membershipDiscount": 0.0, "winnerBonus": bonus, "discount": 0.0, "gloves": [],
               "gloveCharges": 0.0, "frameAmount": charge, "advancePaid": 0.0, "advanceUsed": 0.0,
               "cashCollected": cash, "settlements": [line],
               "passApplied": ([{"memberId": loser["id"], "name": lp["label"], "framesUsed": 1,
                                 "covers": line["passPart"]}] if line["passPart"] else []),
               "winnerCredits": [{"pid": wp["pid"], "memberId": winner["id"], "label": wp["label"], "amount": bonus}],
               "notes": "", "createdAt": ended, "billedBy": "Raju Bhai",
               "winnerPlayerIds": [wp["pid"]], "loserPlayerIds": [lp["pid"]], "totalAmount": charge,
               "paidAmount": money(charge - line["duePart"]), "dueAmount": line["duePart"],
               "status": "paid" if not line["duePart"] else "unpaid", "paymentMode": "cash"}
        await db.frames.insert_one(doc)
        if cash > 0:
            await db.logs.insert_one({**payment("frames", cash, "cash", f"Frame · {table['name']} · ₹{cash:g}"),
                                      "createdAt": ended, "refType": "frame", "refId": fid})
        await db.logs.insert_one({"id": uid("log"), "clubId": club_id, "tag": "BILLING",
                                  "message": f"Frame billed · {table['name']} · ₹{charge:g} · winners: {wp['label']}",
                                  "actorName": "Raju Bhai", "createdAt": ended, "refType": "frame", "refId": fid})
        return doc

    await frame(snooker, 12, 48, ravi, imran, {"cashPart": 252.0}, hour=20)
    await frame(pool1, 9, 35, arjun, dev, {"cashPart": 135.0}, hour=17)
    await frame(snooker, 7, 60, arjun, ravi, {"walletPart": 270.0}, hour=21,
                items=[{"menuItemId": by_name["Cold Coffee"]["id"], "name": "Cold Coffee", "price": 90, "qty": 1, "amount": 90}])
    await frame(snooker, 4, 52, sana, arjun, {"passPart": 238.0}, hour=19)
    await frame(pool2, 1, 40, imran, arjun, {"walletPart": 40.0, "duePart": 110.0}, hour=20)
    await frame(snooker, 0, 30, ravi, dev, {"cashPart": 150.0}, hour=13)

    # ---- a live session right now on Snooker 1 (Ravi vs Imran)
    await db.sessions.insert_one({
        "id": uid("s"), "clubId": club_id, "tableId": snooker["id"], "tableName": snooker["name"],
        "startedAt": ago(minutes=27), "endedAt": None,
        "players": [player(ravi), player(imran)], "playerCount": 2,
        "hourlyRate": snooker["rate"]["hourlyRate"], "minCharge": snooker["rate"]["minCharge"],
        "peak": False, "matchMode": "solo", "items": [], "itemsTotal": 0, "discount": 0,
        "advancePaid": 0.0, "notes": "", "billingLock": False, "billedBy": None})

    # ---- app orders + counter bills for Ravi and Arjun
    def line(name, qty):
        it = by_name[name]
        return {"menuItemId": it["id"], "name": it["name"], "price": it["price"], "qty": qty,
                "amount": money(it["price"] * qty), "costPrice": it["costPrice"], "costAmount": money(it["costPrice"] * qty)}

    async def bill(member, lines, mode, created, source="member_order", deliver="Counter pickup", **extra):
        amount = money(sum(l["amount"] for l in lines))
        cost = money(sum(l["costAmount"] for l in lines))
        paid = mode != "due"
        doc = {"id": uid("order" if source == "member_order" else "bill"), "clubId": club_id, "items": lines,
               "customerName": member["name"], "memberId": member["id"], "memberName": member["name"],
               "source": source, "mode": mode, "paymentMode": mode,
               "payments": [{"mode": mode, "amount": amount}] if paid else [],
               "subtotal": amount, "discount": 0.0, "amount": amount, "total": amount,
               "costAmount": cost, "profit": money(amount - cost),
               "paidAmount": amount if paid else 0.0, "dueAmount": 0.0 if paid else amount,
               "paid": paid, "status": "paid" if paid else "unpaid",
               "createdAt": created, "paidAt": created if paid else None, "updatedAt": created,
               "createdBy": member["name"] if source == "member_order" else "Raju Bhai", **extra}
        if source == "member_order":
            doc.update({"channel": "members_app", "deliverTo": deliver,
                        "requestedMode": "wallet" if mode == "wallet" else extra.get("requestedMode", "counter"),
                        "fulfilment": extra.get("fulfilment", "served")})
        await db.item_bills.insert_one(doc)
        if paid:
            await db.logs.insert_one({**payment("items", amount, mode, f"{'App order' if source == 'member_order' else 'Item bill'} · {member['name']} · ₹{amount:g} ({mode})", member["id"]),
                                      "createdAt": created, "refType": "item_bill", "refId": doc["id"]})
        return doc

    await bill(ravi, [line("Cold Coffee", 1), line("Samosa", 2)], "wallet", ago(days=3, hours=2), deliver="Table · Snooker 1")
    await bill(ravi, [line("Chai", 2)], "cash", ago(days=1, hours=5), source="counter")
    await bill(ravi, [line("Snooker Chalk · Blue", 2), line("Cue Tips · Elk Master (5)", 1)], "wallet", ago(days=6), deliver="Counter pickup")
    await bill(ravi, [line("Masala Maggi", 1), line("Red Bull", 1)], "wallet", ago(minutes=40),
               deliver="Table · Snooker 1", fulfilment="preparing")
    await bill(arjun, [line("Masala Maggi", 1)], "due", ago(days=1, hours=1), source="counter")
    # Arjun ordered on UPI, awaiting verification (his due already includes it via seed total)
    await db.members.update_one({"id": ravi["id"]}, {"$set": {"walletBalance": 500.0}})

    # ---- membership sale history for the ledger (Ravi bought Gold Wallet 15 days ago — seeded above w/o planId)
    gold = await db.plans.find_one({"clubId": club_id, "name": "Gold Wallet"})
    frame_pass = await db.plans.find_one({"clubId": club_id, "name": "Frame Pass"})
    monthly = await db.plans.find_one({"clubId": club_id, "name": "Monthly"})
    await db.membership_sales.update_many({"clubId": club_id, "memberName": "Ravi Kumar"}, {"$set": {"planId": gold["id"], "createdAt": ago(days=15)}})
    await db.members.update_one({"id": ravi["id"]}, {"$set": {"planId": gold["id"]}})
    await db.membership_sales.insert_one({
        "id": uid("sale"), "clubId": club_id, "memberId": arjun["id"], "memberName": "Arjun Singh",
        "planId": frame_pass["id"], "planName": "Frame Pass", "planType": "pass", "amount": 900, "mode": "cash",
        "origin": "sell", "createdAt": ago(days=10)})
    await db.members.update_one({"id": arjun["id"]}, {"$set": {"planId": frame_pass["id"],
                                                             "planExpiresAt": iso(now_utc() + timedelta(days=20))}})
    await db.members.update_one({"id": sana["id"]}, {"$set": {"planId": monthly["id"],
                                                            "planExpiresAt": iso(now_utc() + timedelta(days=12))}})

    # ---- a pending UPI membership request (Arjun → Monthly) and an approved top-up (Ravi)
    await db.membership_requests.insert_one({
        "id": uid("mreq"), "clubId": club_id, "memberId": arjun["id"], "memberName": "Arjun Singh",
        "planId": monthly["id"], "planName": "Monthly", "planType": "monthly", "amount": 1500,
        "utr": "UPI2309X77812", "status": "pending", "createdAt": ago(hours=3)})
    await db.wallet_topup_requests.insert_one({
        "id": uid("topup"), "clubId": club_id, "memberId": ravi["id"], "memberName": "Ravi Kumar",
        "amount": 300, "utr": "UPI2209Y11009", "status": "approved", "createdAt": ago(days=2, hours=6),
        "approvedAt": ago(days=2, hours=5), "approvedBy": "Raju Bhai", "mode": "upi"})

    # ---- bookings
    def slot(days, start_h, start_m, minutes, table, member, status="upcoming", players=2):
        d = (now_utc().astimezone(IST) + timedelta(days=days)).date()
        start_local = datetime(d.year, d.month, d.day, start_h, start_m, tzinfo=IST)
        end_local = start_local + timedelta(minutes=minutes)
        return {"id": uid("bk"), "clubId": club_id, "tableId": table["id"], "tableName": table["name"],
                "memberId": member["id"], "memberName": member["name"], "dateIST": d.isoformat(),
                "startIST": start_local.strftime("%H:%M"), "endIST": end_local.strftime("%H:%M"),
                "startAt": iso(start_local), "endAt": iso(end_local), "minutes": minutes, "players": players,
                "notes": "", "status": status, "createdAt": ago(days=1), "createdBy": member["name"]}

    for b in (slot(2, 18, 0, 60, snooker, ravi, players=2), slot(5, 20, 0, 90, pool1, ravi, players=4),
              slot(-6, 19, 0, 60, snooker, ravi, status="completed"),
              slot(1, 20, 0, 60, pool1, arjun), slot(-3, 18, 30, 60, pool2, arjun, status="completed"),
              slot(2, 19, 30, 60, pool2, sana)):
        await db.bookings.insert_one(b)

    # ---- upcoming tournament: Monthly Masters (next Saturday)
    d = now_utc().astimezone(IST).date()
    sat = d + timedelta(days=(5 - d.weekday()) % 7 or 7)
    entrants = [{"pid": uid("tp"), "name": ravi["name"], "phone": ravi["phone"], "memberId": ravi["id"],
                 "paidEntry": True, "seed": 1, "entryFee": 300},
                {"pid": uid("tp"), "name": "Kabir Shah", "phone": "9830000031", "memberId": None,
                 "paidEntry": True, "seed": 2, "entryFee": 300},
                {"pid": uid("tp"), "name": "Zaid Ali", "phone": "9830000041", "memberId": None,
                 "paidEntry": False, "seed": 3, "entryFee": 300},
                {"pid": uid("tp"), "name": sana["name"], "phone": sana["phone"], "memberId": sana["id"],
                 "paidEntry": True, "seed": 4, "entryFee": 300}]
    await db.tournaments.insert_one({
        "id": uid("tour"), "clubId": club_id, "name": "Rowdy's Monthly Masters", "game": "Snooker",
        "date": sat.isoformat(), "entryFee": 300, "prize1": 3000, "prize2": 1000, "maxPlayers": 16,
        "tableRate": 240, "format": "knockout", "notes": "Best of 3 frames · 7 PM start · finalists get Den tees",
        "status": "upcoming", "participants": entrants, "matches": [], "collected": 900.0, "tableCharges": 0.0,
        "winnerPid": None, "winnerName": None, "runnerUpName": None, "completedAt": None, "createdAt": ago(days=4)})
    await db.logs.insert_one({**payment("tournaments", 300, "upi", "Tournament entry · Ravi Kumar · Rowdy's Monthly Masters", ravi["id"]),
                              "createdAt": ago(days=3)})


if __name__ == "__main__":
    asyncio.run(main())
