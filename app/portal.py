"""Members-app service layer — everything the member sees, computed in ONE place.

Used by both `/api/member-portal/*` (JSON) and `/members/*` (Jinja pages) so the
two never drift. Read-only helpers here; money-moving mutations live in
`routers/member_portal.py` next to their validation.

Domain recap (see README): a member has a prepaid `walletBalance`, an aggregate
`dueAmount` (the counter's source of truth), `passFramesLeft` and an optional
plan. Frames (billed games) carry per-player `settlements`; item bills carry
`items` + payment state; both feed the wallet ledger + due breakdown below.
"""
from collections import defaultdict
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException

from . import db as db_mod
from .services import bill_web_aliases, frame_web_aliases, public_member
from .util import money, now_iso, now_utc, parse_iso, IST

# ------------------------------------------------------------------ catalogue
# Cafe vs Pro-shop split. Owners set free-text categories in the billing app —
# anything that smells like food/drink is "cafe", the rest is "shop". An explicit
# `kind` on the item always wins (the seed + future owner UI set it).
CAFE_WORDS = {
    "cafe", "café", "food", "foods", "snack", "snacks", "beverage", "beverages",
    "drink", "drinks", "cold drink", "cold drinks", "soft drink", "soft drinks",
    "coffee", "tea", "chai", "juice", "juices", "shake", "shakes", "smoothie",
    "smoothies", "dessert", "desserts", "sweets", "bakery", "cake", "cakes",
    "ice cream", "icecream", "meal", "meals", "breakfast", "lunch", "dinner",
    "fast food", "burger", "burgers", "pizza", "sandwich", "sandwiches", "noodles",
    "maggi", "fries", "combo", "combos", "water", "energy drink", "energy drinks",
    "biscuits", "chips", "namkeen", "kitchen", "canteen",
}

CATEGORY_EMOJI = [
    (("coffee", "cold coffee", "espresso", "latte"), "☕"),
    (("chai", "tea"), "🫖"),
    (("cola", "soda", "cold drink", "soft drink", "drinks", "beverage", "energy"), "🥤"),
    (("water",), "💧"),
    (("juice", "shake", "smoothie"), "🧃"),
    (("fries", "chips", "snack", "namkeen", "biscuit"), "🍟"),
    (("samosa", "pakora", "kachori"), "🥟"),
    (("sandwich", "burger"), "🥪"),
    (("pizza",), "🍕"),
    (("maggi", "noodle", "pasta"), "🍜"),
    (("cake", "brownie", "dessert", "sweet", "pastry"), "🍰"),
    (("ice cream", "icecream", "gelato"), "🍨"),
    (("meal", "thali", "rice", "biryani", "lunch", "dinner", "food", "cafe", "café"), "🍽️"),
    (("cue", "cues", "stick"), "🎱"),
    (("chalk",), "🟦"),
    (("tip", "tips", "ferrule"), "🔘"),
    (("glove", "gloves"), "🧤"),
    (("shirt", "tee", "apparel", "cloth", "wear", "hoodie", "jersey", "merch"), "👕"),
    (("cap", "hat"), "🧢"),
    (("case", "bag"), "🧳"),
    (("towel", "cloth"), "🧻"),
    (("accessor", "holder", "extension", "rest", "bridge", "gear"), "🧰"),
    (("membership", "plan", "pass"), "🎟️"),
]

SHOP_CATEGORY_ORDER = ("cue", "chalk", "tip", "glove", "apparel", "shirt", "cap", "case", "accessor")


def item_kind(item: dict) -> str:
    kind = (item.get("kind") or "").strip().lower()
    if kind in ("cafe", "shop"):
        return kind
    cat = (item.get("category") or "").strip().lower()
    if not cat or cat in CAFE_WORDS:
        return "cafe"
    for w in CAFE_WORDS:
        if len(w) > 3 and w in cat:
            return "cafe"
    return "shop"


def emoji_for(*texts: str) -> str:
    hay = " ".join(t or "" for t in texts).lower()
    for keys, emo in CATEGORY_EMOJI:
        if any(k in hay for k in keys):
            return emo
    return "🛍️"


def image_url(item: dict) -> str:
    """Items store either a data-URL (owner upload) or a static path. Data URLs
    are served through `/members/img/{id}` so catalogue pages stay light."""
    img = (item.get("image") or "").strip()
    if not img:
        return ""
    if img.startswith("data:"):
        return f"/members/img/{item['id']}"
    return img


def public_item(item: dict, sold: Optional[dict] = None) -> dict:
    """Member-safe item view — never leaks cost price / reorder levels."""
    stock = int(item.get("stockQty") or 0)
    kind = item_kind(item)
    return {
        "id": item["id"], "name": item.get("name", ""), "category": item.get("category") or ("Cafe" if kind == "cafe" else "Shop"),
        "price": money(item.get("price") or 0), "unit": item.get("unit") or "pc",
        "stockQty": stock, "inStock": stock > 0 and item.get("active", True) is not False,
        "lowStock": 0 < stock <= 3, "kind": kind,
        "image": image_url(item), "emoji": emoji_for(item.get("name"), item.get("category")),
        "description": (item.get("description") or "").strip(),
        "tags": item.get("tags") or [],
        "sold": int((sold or {}).get(item["id"], 0)),
        "createdAt": item.get("createdAt") or "",
        "featured": bool(item.get("featured")),
    }


async def sold_counts(db, club_id: str, days: int = 90) -> dict:
    """Units sold per item over the last N days — powers 'Popular'."""
    since = (now_utc() - timedelta(days=days)).isoformat()
    bills = await db.item_bills.find({"clubId": club_id}, sort=[("createdAt", -1)]).to_list(600)
    out: dict = defaultdict(int)
    for b in bills:
        if (b.get("createdAt") or "") < since:
            continue
        for li in b.get("items") or []:
            out[li.get("menuItemId")] += int(li.get("qty") or 0)
    return out


async def catalog(db, club_id: str, include_out_of_stock: bool = True) -> dict:
    items = await db.menu_items.find({"clubId": club_id, "active": {"$ne": False}}).to_list(None)
    sold = await sold_counts(db, club_id)
    views = [public_item(i, sold) for i in items]
    if not include_out_of_stock:
        views = [v for v in views if v["inStock"]]
    cafe = [v for v in views if v["kind"] == "cafe"]
    shop = [v for v in views if v["kind"] == "shop"]

    def cats(rows):
        seen: dict = {}
        for r in rows:
            c = seen.setdefault(r["category"], {"name": r["category"], "count": 0,
                                                "emoji": emoji_for(r["category"], r["name"])})
            c["count"] += 1
        return list(seen.values())

    popular = sorted([v for v in views if v["inStock"]], key=lambda v: (-v["sold"], v["name"]))
    newest = sorted([v for v in views if v["inStock"]], key=lambda v: v["createdAt"], reverse=True)
    return {
        "items": views, "cafe": cafe, "shop": shop,
        "cafeCategories": cats(cafe), "shopCategories": cats(shop),
        "popular": popular[:10], "newArrivals": newest[:10],
        "featured": [v for v in views if v["featured"] and v["inStock"]][:6],
    }


# --------------------------------------------------------------- live tables
def _session_view(s: dict) -> dict:
    return {
        "id": s.get("id"), "startedAt": s.get("startedAt"), "endedAt": s.get("endedAt"),
        "players": [{"label": p.get("label", ""), "memberId": p.get("memberId")}
                    for p in s.get("players") or []],
        "matchMode": s.get("matchMode", "solo"), "peak": bool(s.get("peak")),
    }


async def live_tables(db, club: dict, member_id: Optional[str] = None) -> list[dict]:
    tables = await db.tables.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    tables.sort(key=lambda t: (t.get("sortOrder", 0), t.get("name", "")))
    sessions = await db.sessions.find({"clubId": club["id"]}).to_list(None)
    by_table = {s.get("tableId"): s for s in sessions}
    out = []
    for t in tables:
        s = by_table.get(t["id"])
        rate = t.get("rate") or {}
        view = {
            "id": t["id"], "name": t.get("name", "Table"),
            "game": "Pool" if "pool" in t.get("name", "").lower() else "Snooker",
            "hourlyRate": money(rate.get("hourlyRate") or 0),
            "minCharge": money(rate.get("minCharge") or 0),
            "ratesByPlayers": rate.get("ratesByPlayers") or {},
            "peakHourlyRate": money(rate["peakHourlyRate"]) if rate.get("peakHourlyRate") else None,
            "peakStartHour": rate.get("peakStartHour"), "peakEndHour": rate.get("peakEndHour"),
            "glovePrice": money(rate.get("glovePrice") or 0),
            "status": "free", "session": None, "mine": False,
        }
        if s:
            view["status"] = "live" if not s.get("endedAt") else "billing"
            view["session"] = _session_view(s)
            view["mine"] = any(p.get("memberId") == member_id for p in s.get("players") or [])
        out.append(view)
    return out


# -------------------------------------------------------------------- frames
def frame_view(frame: dict, member_id: str) -> Optional[dict]:
    """The frame from ONE member's seat: did I win, what did I pay and how."""
    f = frame_web_aliases(dict(frame))
    me = next((p for p in f.get("players") or [] if p.get("memberId") == member_id), None)
    if not me:
        return None
    win_pids = set(f.get("winnersPids") or f.get("winnerPlayerIds") or [])
    won = me.get("pid") in win_pids or bool(me.get("isWinner"))
    line = next((s for s in f.get("settlements") or [] if s.get("memberId") == member_id), None)
    credit = next((c for c in f.get("winnerCredits") or [] if c.get("memberId") == member_id), None)
    parts = []
    if line:
        for key, label in (("walletPart", "Wallet"), ("passPart", "Frame pass"),
                           ("cashPart", "Cash at counter"), ("duePart", "Added to due")):
            v = money(line.get(key) or 0)
            if v > 0:
                parts.append({"label": label, "amount": v, "key": key})
    players = []
    for p in f.get("players") or []:
        players.append({"label": p.get("label", "?"), "memberId": p.get("memberId"),
                        "isWinner": p.get("pid") in win_pids, "team": p.get("team"),
                        "me": p.get("memberId") == member_id})
    return {
        "id": f["id"], "tableName": f.get("tableName", ""), "startedAt": f.get("startedAt"),
        "endedAt": f.get("endedAt"), "createdAt": f.get("createdAt"),
        "durationMinutes": int(f.get("durationMinutes") or 0),
        "matchMode": f.get("matchMode", "solo"), "peak": bool(f.get("peak")),
        "players": players, "won": won,
        "winners": f.get("winners") or [], "losers": f.get("losers") or [],
        "frameAmount": money(f.get("frameAmount") or 0),
        "tableAmount": money(f.get("tableAmount") or 0),
        "itemsAmount": money(f.get("itemsAmount") or 0),
        "myCharge": money(line.get("charge") or 0) if line else 0.0,
        "myDiscount": money(line.get("membershipDiscount") or 0) if line else 0.0,
        "myDue": money(line.get("duePart") or 0) if line else 0.0,
        "myParts": parts,
        "bonus": money(credit.get("amount") or 0) if credit else 0.0,
        "items": [{"name": i.get("name"), "qty": i.get("qty"), "amount": money(i.get("amount") or 0)}
                  for i in f.get("items") or []],
        "gloves": [g.get("label") for g in f.get("gloves") or []],
    }


async def my_frames(db, club: dict, member_id: str, limit: int = 300) -> list[dict]:
    frames = await db.frames.find({"clubId": club["id"]}, sort=[("createdAt", -1)]).to_list(limit)
    out = []
    for fr in frames:
        v = frame_view(fr, member_id)
        if v:
            out.append(v)
    out.sort(key=lambda v: v.get("endedAt") or v.get("createdAt") or "", reverse=True)
    return out


def frame_stats(frames: list[dict]) -> dict:
    played = len(frames)
    won = sum(1 for f in frames if f["won"])
    streak, kind = 0, None
    for f in frames:  # newest first
        k = "W" if f["won"] else "L"
        if kind is None:
            kind = k
        if k != kind:
            break
        streak += 1
    minutes = sum(f["durationMinutes"] for f in frames)
    tables: dict = defaultdict(int)
    for f in frames:
        tables[f["tableName"]] += 1
    fav = max(tables.items(), key=lambda kv: kv[1])[0] if tables else None
    spent = money(sum(f["myCharge"] for f in frames))
    bonus = money(sum(f["bonus"] for f in frames))
    return {"played": played, "won": won, "lost": played - won,
            "winRate": round(won * 100 / played) if played else 0,
            "streak": streak, "streakKind": kind, "minutes": minutes,
            "hours": round(minutes / 60, 1), "favouriteTable": fav,
            "spent": spent, "bonus": bonus}


# --------------------------------------------------------------------- bills
def bill_view(bill: dict) -> dict:
    b = bill_web_aliases(dict(bill))
    outstanding = money(b.get("dueAmount") or 0)
    status = b.get("status") or ("paid" if b.get("paid") else "unpaid")
    verification = b.get("verification")
    requested = b.get("requestedMode") or b.get("mode")
    if status != "paid" and verification == "pending":
        status_label = "UPI · awaiting confirmation"
    elif status == "paid":
        status_label = "Paid"
    elif status == "partial":
        status_label = "Partly paid"
    else:
        status_label = "Pay at counter" if requested in ("counter", "due") else "Unpaid"
    pay_modes = sorted({p.get("mode") for p in b.get("payments") or [] if p.get("mode")})
    return {
        "id": b["id"], "createdAt": b.get("createdAt"), "paidAt": b.get("paidAt"),
        "items": [{"name": i.get("name"), "qty": int(i.get("qty") or 0),
                   "price": money(i.get("price") or 0), "amount": money(i.get("amount") or 0),
                   "menuItemId": i.get("menuItemId")} for i in b.get("items") or []],
        "count": sum(int(i.get("qty") or 0) for i in b.get("items") or []),
        "subtotal": money(b.get("subtotal") or b.get("amount") or 0),
        "discount": money(b.get("discount") or 0),
        "amount": money(b.get("amount") or 0), "paidAmount": money(b.get("paidAmount") or 0),
        "dueAmount": outstanding, "paid": bool(b.get("paid")), "status": status,
        "statusLabel": status_label, "mode": b.get("mode"), "requestedMode": requested,
        "payModes": pay_modes, "verification": verification, "paymentRef": b.get("paymentRef"),
        "source": "app" if b.get("source") == "member_order" else "counter",
        "deliverTo": b.get("deliverTo") or "", "note": b.get("note") or "",
        "fulfilment": b.get("fulfilment") or ("served" if b.get("source") != "member_order" else "placed"),
        "settledMode": b.get("settledMode"),
    }


async def my_bills(db, club: dict, member_id: str, limit: int = 200) -> list[dict]:
    bills = await db.item_bills.find({"clubId": club["id"], "memberId": member_id},
                                     sort=[("createdAt", -1)]).to_list(limit)
    views = [bill_view(b) for b in bills]
    views.sort(key=lambda v: v.get("createdAt") or "", reverse=True)
    return views


# ---------------------------------------------------------- dues + ledger
def due_breakdown(member: dict, frames: list[dict], bills: list[dict]) -> dict:
    """`member.dueAmount` is the truth (the counter collects against it). Frames
    and bills only tell us WHERE the due came from — walk newest-first until
    the recorded charges explain the balance; anything older is 'opening'."""
    total = money(member.get("dueAmount") or 0)
    entries = []
    for f in frames:
        if f["myDue"] > 0:
            entries.append({"kind": "frame", "id": f["id"], "at": f.get("endedAt") or f.get("createdAt"),
                            "title": f"Frame · {f['tableName']}", "amount": f["myDue"],
                            "subtitle": f"{f['durationMinutes']} min · lost to {', '.join(f['winners']) or '—'}"})
    for b in bills:
        if b["dueAmount"] > 0:
            entries.append({"kind": "bill", "id": b["id"], "at": b["createdAt"],
                            "title": "Cafe / store bill" if b["source"] == "counter" else "App order",
                            "amount": b["dueAmount"],
                            "subtitle": ", ".join(f"{i['name']}×{i['qty']}" for i in b["items"])[:80],
                            "pendingUpi": b["verification"] == "pending"})
    entries.sort(key=lambda e: e["at"] or "", reverse=True)
    shown, acc = [], 0.0
    if total > 0:
        for e in entries:
            if acc >= total:
                break
            shown.append(e)
            acc = money(acc + e["amount"])
    other = money(max(0.0, total - acc))
    return {"total": total, "entries": shown, "other": other,
            "explained": money(min(acc, total))}


async def wallet_ledger(db, club: dict, member: dict, frames: list[dict],
                        bills: list[dict]) -> list[dict]:
    """Derived transaction history. The system stores no wallet ledger and frame
    wallet debits are never payment-logged, so we read the documents themselves:
    frames (settlements + winner credits), bills (payments), plan sales, approved
    top-ups and due collections. Positive amount = credit, negative = debit,
    0 = informational (cash at counter / added to tab / pass used)."""
    mid = member["id"]
    rows: list[dict] = []
    for f in frames:
        at = f.get("endedAt") or f.get("createdAt")
        for part in f["myParts"]:
            amt = part["amount"]
            if part["key"] == "walletPart":
                rows.append({"at": at, "title": f"Table time · {f['tableName']}", "amount": -amt,
                             "kind": "frame", "sub": f"{f['durationMinutes']} min · paid from wallet", "ref": f["id"]})
            elif part["key"] == "passPart":
                rows.append({"at": at, "title": f"Frame pass used · {f['tableName']}", "amount": 0,
                             "kind": "pass", "sub": f"1 frame · covered ₹{amt:g}", "ref": f["id"]})
            elif part["key"] == "duePart":
                rows.append({"at": at, "title": f"Added to tab · {f['tableName']}", "amount": 0,
                             "kind": "due", "due": amt, "sub": f"₹{amt:g} due on this frame", "ref": f["id"]})
            elif part["key"] == "cashPart":
                rows.append({"at": at, "title": f"Paid at counter · {f['tableName']}", "amount": 0,
                             "kind": "cash", "sub": f"₹{amt:g} cash / UPI", "ref": f["id"]})
        if f["bonus"] > 0:
            rows.append({"at": at, "title": "Winner bonus", "amount": f["bonus"], "kind": "bonus",
                         "sub": f"Won on {f['tableName']}", "ref": f["id"]})

    raw_bills = await db.item_bills.find({"clubId": club["id"], "memberId": mid},
                                         sort=[("createdAt", -1)]).to_list(200)
    payments_by_id = {b["id"]: (b.get("payments") or []) for b in raw_bills}
    for b in bills:
        label = "App order" if b["source"] == "app" else "Cafe / store bill"
        items = ", ".join(f"{i['name']}×{i['qty']}" for i in b["items"])[:60]
        pays = payments_by_id.get(b["id"], [])
        wallet_paid = money(sum(p.get("amount", 0) for p in pays if p.get("mode") == "wallet"))
        other_paid = money(sum(p.get("amount", 0) for p in pays if p.get("mode") != "wallet"))
        base = {"at": b["createdAt"], "title": label, "ref": b["id"]}
        if wallet_paid > 0:
            rows.append({**base, "amount": -wallet_paid, "kind": "bill", "sub": items + " · wallet"})
        if other_paid > 0:
            rows.append({**base, "amount": 0, "kind": "cash", "sub": items + " · paid at counter"})
        if b["dueAmount"] > 0:
            rows.append({**base, "amount": 0, "kind": "due", "due": b["dueAmount"],
                         "sub": items + (" · UPI pending" if b["verification"] == "pending" else " · on your tab")})

    sales = await db.membership_sales.find({"clubId": club["id"], "memberId": mid},
                                           sort=[("createdAt", -1)]).to_list(100)
    plans = {p["id"]: p for p in await db.plans.find({"clubId": club["id"]}).to_list(None)}
    for s in sales:
        plan = plans.get(s.get("planId")) or {}
        if s.get("planType") == "wallet":
            credit = money(plan.get("value") or s.get("amount") or 0)
            rows.append({"at": s["createdAt"], "title": f"Wallet recharge · {s.get('planName', 'plan')}",
                         "amount": credit, "kind": "topup",
                         "sub": f"Paid ₹{money(s.get('amount') or 0):g} via {s.get('mode', '—')}", "ref": s["id"]})
        else:
            from_wallet = s.get("mode") == "wallet"
            rows.append({"at": s["createdAt"], "title": f"Plan · {s.get('planName', 'membership')}",
                         "amount": -money(s.get("amount") or 0) if from_wallet else 0, "kind": "plan",
                         "sub": "Paid from wallet" if from_wallet else f"Paid via {s.get('mode', '—')}",
                         "ref": s["id"]})

    topups = await db.wallet_topup_requests.find({"clubId": club["id"], "memberId": mid}).to_list(100)
    for t in topups:
        if t.get("status") == "approved":
            rows.append({"at": t.get("approvedAt") or t["createdAt"], "title": "Wallet top-up",
                         "amount": money(t.get("amount") or 0), "kind": "topup",
                         "sub": f"UPI ref {t.get('utr', '')}", "ref": t["id"]})

    logs = await db.logs.find({"clubId": club["id"], "memberId": mid, "tag": "PAYMENT", "type": "due"},
                              sort=[("createdAt", -1)]).to_list(200)
    for l in logs:
        amt = money(l.get("amount") or 0)
        if l.get("mode") == "wallet":
            rows.append({"at": l["createdAt"], "title": "Tab cleared from wallet", "amount": -amt,
                         "kind": "duepay", "sub": "Due paid from wallet", "ref": l["id"]})
        elif l.get("refType") != "frame":
            rows.append({"at": l["createdAt"], "title": "Tab paid at counter", "amount": 0, "kind": "cash",
                         "sub": f"₹{amt:g} via {l.get('mode', '—')}", "ref": l["id"]})
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows


# ------------------------------------------------------------------ bookings
async def my_bookings(db, club: dict, member_id: str) -> list[dict]:
    from .routers.bookings import _sweep
    await _sweep(db, club["id"])
    rows = await db.bookings.find({"clubId": club["id"], "memberId": member_id}).to_list(300)
    for b in rows:
        b.pop("_id", None)
    rows.sort(key=lambda b: b.get("startAt", ""), reverse=True)
    return rows


# --------------------------------------------------------------- tournaments
def tournament_view(t: dict, member_id: str) -> dict:
    players = t.get("participants") or []
    mine = next((p for p in players if p.get("memberId") == member_id), None)
    matches = t.get("matches") or []
    done = [m for m in matches if m.get("status") in ("done", "played")]
    live = [m for m in matches if m.get("status") == "table_live"]
    return {
        "id": t["id"], "name": t.get("name", ""), "game": t.get("game", "Snooker"),
        "date": t.get("date", ""), "entryFee": money(t.get("entryFee") or 0),
        "prize1": money(t.get("prize1") or 0), "prize2": money(t.get("prize2") or 0),
        "maxPlayers": int(t.get("maxPlayers") or 0), "playerCount": len(players),
        "format": t.get("format", "knockout"), "status": t.get("status", "upcoming"),
        "notes": t.get("notes") or "", "winnerName": t.get("winnerName"),
        "runnerUpName": t.get("runnerUpName"),
        "registered": bool(mine), "paidEntry": bool(mine and mine.get("paidEntry")),
        "spotsLeft": max(0, int(t.get("maxPlayers") or 0) - len(players)),
        "players": [{"name": p.get("name", "?"), "paid": bool(p.get("paidEntry")),
                     "me": p.get("memberId") == member_id} for p in players],
        "matchesDone": len(done), "matchesLive": len(live), "matchesTotal": len(matches),
        "liveMatches": [{"p1": (m.get("p1") or {}).get("name", "?"), "p2": (m.get("p2") or {}).get("name", "?"),
                         "tableName": m.get("tableName", "")} for m in live],
        "myMatches": [{"p1": (m.get("p1") or {}).get("name", "?"), "p2": (m.get("p2") or {}).get("name", "?"),
                       "status": m.get("status"), "round": m.get("round"),
                       "score1": m.get("score1"), "score2": m.get("score2"),
                       "won": bool(mine and m.get("winnerPid") == mine.get("pid"))}
                      for m in matches if mine and mine.get("pid") in ((m.get("p1") or {}).get("pid"), (m.get("p2") or {}).get("pid"))],
    }


async def club_tournaments(db, club: dict, member_id: str) -> list[dict]:
    tours = await db.tournaments.find({"clubId": club["id"]}).to_list(None)
    views = [tournament_view(t, member_id) for t in tours]
    order = {"running": 0, "upcoming": 1, "completed": 2, "cancelled": 3}
    views.sort(key=lambda v: (order.get(v["status"], 9), v["date"] if v["status"] == "upcoming" else ""), reverse=False)
    # upcoming soonest first; past newest first
    up = sorted([v for v in views if v["status"] in ("upcoming", "running")], key=lambda v: (v["status"] != "running", v["date"]))
    past = sorted([v for v in views if v["status"] not in ("upcoming", "running")], key=lambda v: v["date"], reverse=True)
    return up + past


# ---------------------------------------------------------------- membership
def plan_view(plan: dict, member: dict) -> dict:
    t = plan.get("type")
    if t == "wallet":
        credit = money(plan.get("value") or plan.get("amount") or 0)
        bonus = money(credit - money(plan.get("amount") or 0))
        benefit = f"₹{credit:g} wallet credit" + (f" · ₹{bonus:g} bonus" if bonus > 0 else "")
        icon = "wallet"
    elif t == "pass":
        frames = int(plan.get("frames") or plan.get("value") or 0)
        benefit = f"{frames} frames" + (f" · valid {plan.get('days')} days" if plan.get("days") else "")
        icon = "ticket"
    else:
        pct = plan.get("tableDiscountPercent") or 0
        benefit = f"{pct:g}% off table time" + (f" · {plan.get('days')} days" if plan.get("days") else "")
        icon = "crown"
    return {
        "id": plan["id"], "name": plan.get("name", ""), "type": t, "amount": money(plan.get("amount") or 0),
        "value": money(plan.get("value") or 0), "frames": int(plan.get("frames") or 0),
        "days": int(plan.get("days") or 0), "tableDiscountPercent": plan.get("tableDiscountPercent"),
        "description": plan.get("description") or "", "benefit": benefit, "icon": icon,
        "isDefault": bool(plan.get("isDefault")), "current": plan["id"] == member.get("planId"),
    }


def membership_status(member: dict) -> dict:
    exp = member.get("planExpiresAt")
    days_left, expired = None, False
    if exp:
        try:
            delta = parse_iso(exp) - now_utc()
            days_left = max(0, delta.days + (1 if delta.seconds > 0 else 0))
            expired = delta.total_seconds() <= 0
        except ValueError:
            pass
    return {
        "planId": member.get("planId"), "planName": member.get("planName"),
        "planType": member.get("planType"), "expiresAt": exp, "daysLeft": days_left,
        "expired": expired, "tableDiscountPercent": member.get("tableDiscountPercent") or 0,
        "passFramesLeft": int(member.get("passFramesLeft") or 0),
        "active": bool(member.get("planId") or member.get("planName")) and not expired,
    }


# ------------------------------------------------------------------ activity
ACTIVITY_ICON = {"PAYMENT": "receipt", "BILLING": "table", "WARNING": "alert", "ADMIN": "shield"}


async def my_activity(db, club: dict, member: dict, seen_at: Optional[str], limit: int = 120) -> dict:
    logs = await db.logs.find({"clubId": club["id"], "memberId": member["id"]},
                              sort=[("createdAt", -1)]).to_list(limit)
    rows = []
    for l in logs:
        rows.append({"id": l["id"], "at": l["createdAt"], "tag": l.get("tag", ""),
                     "type": l.get("type"), "message": l.get("message", ""),
                     "amount": money(l.get("amount")) if l.get("amount") is not None else None,
                     "mode": l.get("mode"), "icon": ACTIVITY_ICON.get(l.get("tag", ""), "dot"),
                     "unread": bool(seen_at and l["createdAt"] > seen_at) or (not seen_at)})
    unread = sum(1 for r in rows if r["unread"])
    return {"items": rows, "unread": unread}


# ------------------------------------------------------------------ requests
async def my_requests(db, club: dict, member_id: str) -> dict:
    mreq = await db.membership_requests.find({"clubId": club["id"], "memberId": member_id}).to_list(50)
    treq = await db.wallet_topup_requests.find({"clubId": club["id"], "memberId": member_id}).to_list(50)
    for r in mreq + treq:
        r.pop("_id", None)
    mreq.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    treq.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    return {"membership": mreq, "topups": treq,
            "pending": [r for r in mreq + treq if r.get("status") == "pending"]}


# ------------------------------------------------------------------- context
async def member_context(user: dict):
    """(db, member, club) for the signed-in member or 403."""
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


def public_club(club: dict) -> dict:
    s = club.get("settings") or {}
    return {
        "id": club["id"], "name": club.get("name", "Club"), "logo": club.get("logo") or "",
        "hasQr": bool(club.get("qrCode")), "isOpen": s.get("isOpen", True) is not False,
        "currencySymbol": s.get("currencySymbol") or "₹", "winnerBonus": money(s.get("winnerBonus") or 0),
        "dueLimit": money(s.get("dueLimit") or 0), "monthlyTableDiscount": s.get("monthlyTableDiscount") or 0,
        "phone": club.get("phone") or s.get("phone") or "", "address": club.get("address") or s.get("address") or "",
        "hours": club.get("hours") or s.get("hours") or "8:00 AM – 11:30 PM",
        "upiId": club.get("upiId") or s.get("upiId") or "",
    }


def member_summary(member: dict) -> dict:
    m = public_member(member)
    return {
        "id": m["id"], "name": m.get("name", "Member"), "phone": m.get("phone") or "",
        "email": m.get("email") or "", "walletBalance": money(m.get("walletBalance") or 0),
        "dueAmount": money(m.get("dueAmount") or 0), "passFramesLeft": int(m.get("passFramesLeft") or 0),
        "badge": m.get("badge"), "planName": m.get("planName"), "planType": m.get("planType"),
        "planExpiresAt": m.get("planExpiresAt"), "tableDiscountPercent": m.get("tableDiscountPercent") or 0,
        "createdAt": m.get("createdAt"), "notes": "",
        "membership": membership_status(m),
    }


async def home_payload(db, member: dict, club: dict, user: dict) -> dict:
    """Everything the home screen needs in one round-trip."""
    mid = member["id"]
    tables = await live_tables(db, club, mid)
    frames = await my_frames(db, club, mid, limit=200)
    bills = await my_bills(db, club, mid, limit=100)
    cat = await catalog(db, club["id"])
    bookings = await my_bookings(db, club, mid)
    upcoming = [b for b in bookings if b.get("status") == "upcoming"]
    upcoming.sort(key=lambda b: b.get("startAt", ""))
    tours = await club_tournaments(db, club, mid)
    act = await my_activity(db, club, member, user.get("activitySeenAt"), limit=60)
    reqs = await my_requests(db, club, mid)
    return {
        "member": member_summary(member), "club": public_club(club),
        "tables": tables, "liveCount": sum(1 for t in tables if t["status"] != "free"),
        "dues": due_breakdown(member, frames, bills),
        "recentFrames": frames[:3], "stats": frame_stats(frames),
        "unpaidBills": [b for b in bills if b["dueAmount"] > 0],
        "recentOrders": bills[:3],
        "popular": cat["popular"][:6], "newArrivals": cat["newArrivals"][:6],
        "cafe": cat["cafe"][:6], "shop": cat["shop"][:6], "featured": cat["featured"],
        "nextBooking": upcoming[0] if upcoming else None, "upcomingBookings": upcoming[:3],
        "tournaments": [t for t in tours if t["status"] in ("upcoming", "running")][:2],
        "unread": act["unread"], "pendingRequests": reqs["pending"],
        "serverNow": now_iso(),
    }
