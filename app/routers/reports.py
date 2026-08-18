"""Reports (OWNER ONLY — staff 403): monthly revenue sheet, finance (P&L,
balance sheet, stock profit), day close, table utilisation & peak hours.

Income = the PAYMENT ledger (log entries tagged PAYMENT), cash-basis by
club-local (IST) day. `mode: wallet` entries are pre-paid consumption, never
counted as new income (the membership sale already booked it)."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db as db_mod
from ..auth import current_user
from ..services import deny_staff_admin, get_club, wants_web
from ..util import days_in_month, ist_date, ist_hour, ist_month, money, month_of, today_ist

router = APIRouter(prefix="/clubs/{club_id}/reports", tags=["reports"])
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _month_or_400(month: str) -> str:
    if not MONTH_RE.match(month or "") or not (1 <= int(month[5:7]) <= 12):
        raise HTTPException(400, "Month must look like YYYY-MM")
    return month


async def _load(db, club_id: str):
    logs = await db.logs.find({"clubId": club_id, "tag": "PAYMENT"}).to_list(None)
    expenses = await db.expenses.find({"clubId": club_id}).to_list(None)
    bills = await db.item_bills.find({"clubId": club_id}).to_list(None)
    frames = await db.frames.find({"clubId": club_id}).to_list(None)
    members = await db.members.find({"clubId": club_id}).to_list(None)
    items = await db.menu_items.find({"clubId": club_id}).to_list(None)
    tables = await db.tables.find({"clubId": club_id}).to_list(None)
    return logs, expenses, bills, frames, members, items, tables


def _payments_in(logs, ym: str):
    return [l for l in logs if ist_month(l["createdAt"]) == ym]


def _income_rows(payments):
    """Rows for the monthly sheet + income buckets (wallet-mode excluded)."""
    return [l for l in payments if l.get("mode") != "wallet"]


def _source_totals(rows):
    totals = {"frames": 0.0, "items": 0.0, "memberships": 0.0, "due": 0.0,
              "tournaments": 0.0}
    counts = dict(totals)
    for l in rows:
        src = l.get("type", "frames")
        if src not in totals:
            src = "frames"
        totals[src] += l.get("amount", 0)
        counts[src] += 1
    return {k: money(v) for k, v in totals.items()}, counts


def _daily(rows, expenses, ym: str):
    days = {}
    for l in rows:
        d = ist_date(l["createdAt"])
        if not d.startswith(ym):
            continue
        days.setdefault(d, 0.0)
        days[d] += l.get("amount", 0)
    by_day = []
    for l in rows:
        d = ist_date(l["createdAt"])
        if not d.startswith(ym):
            continue
        entry = next((e for e in by_day if e["date"] == d), None)
        if not entry:
            entry = {"date": d, "frames": 0.0, "items": 0.0, "memberships": 0.0,
                     "due": 0.0, "tournaments": 0.0, "income": 0.0, "expenses": 0.0}
            by_day.append(entry)
        src = l.get("type", "frames")
        if src not in ("frames", "items", "memberships", "due", "tournaments"):
            src = "frames"
        entry[src] += l.get("amount", 0)
        entry["income"] += l.get("amount", 0)
    for e in expenses:
        d = e.get("date", "")
        if not d.startswith(ym):
            continue
        entry = next((x for x in by_day if x["date"] == d), None)
        if not entry:
            entry = {"date": d, "frames": 0.0, "items": 0.0, "memberships": 0.0,
                     "due": 0.0, "tournaments": 0.0, "income": 0.0, "expenses": 0.0}
            by_day.append(entry)
        entry["expenses"] += e.get("amount", 0)
    by_day.sort(key=lambda x: x["date"])
    running = 0.0
    for entry in by_day:
        for k in ("frames", "items", "memberships", "due", "tournaments",
                  "income", "expenses"):
            entry[k] = money(entry[k])
        entry["dueCollections"] = entry["due"]  # web alias (daily row)
        entry["net"] = money(entry["income"] - entry["expenses"])
        entry["total"] = entry["income"]
        running = money(running + entry["net"])
        entry["running"] = running
        entry["balance"] = running  # web alias (Finance daily table / closing)
    return by_day


# web monthly rows ko Title-case source labels chahiye (Badge color isi pe hai);
# canonical (Flutter) raw type codes hi rakhta hai
WEB_SOURCE_LABELS = {"frames": "Frame", "items": "Item Bill",
                     "memberships": "Membership", "due": "Due Collection",
                     "tournaments": "Tournament"}


@router.get("/monthly")
async def monthly_report(request: Request, club_id: str, month: str = "",
                         user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    ym = _month_or_400(month) if month else month_of(today_ist())
    db = await db_mod.get_db()
    logs, expenses, *_ = await _load(db, club["id"])
    payments = _payments_in(logs, ym)
    rows = sorted(_income_rows(payments), key=lambda l: l["createdAt"], reverse=True)
    totals, counts = _source_totals(rows)
    web = wants_web(request)
    sheet_rows = [{
        "id": l["id"], "date": ist_date(l["createdAt"]),
        "source": (WEB_SOURCE_LABELS.get(l.get("type", "frames"), "Frame")
                   if web else l.get("type", "frames")),
        "desc": l.get("message", ""), "amount": l.get("amount", 0),
        "mode": l.get("mode", "cash"),
        "label": l.get("message", ""), "createdAt": l["createdAt"],  # web aliases
    } for l in rows]
    month_total = money(sum(totals.values()))
    return {
        "month": ym,
        "sourceTotals": {**totals, "dueCollections": totals["due"],  # web alias
                         "total": month_total},
        "sourceCounts": counts,
        "counts": {  # web alias — counts.frames / itemBills / duePayments
            "payments": sum(counts.values()),
            "frames": counts.get("frames", 0),
            "itemBills": counts.get("items", 0),
            "memberships": counts.get("memberships", 0),
            "duePayments": counts.get("due", 0),
            "tournaments": counts.get("tournaments", 0),
        },
        "totalEarnings": month_total,
        "daily": _daily(rows, expenses, ym),
        "rows": sheet_rows,
    }


@router.get("/finance")
async def finance_report(request: Request, club_id: str, month: str = "", user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    ym = _month_or_400(month) if month else month_of(today_ist())
    db = await db_mod.get_db()
    logs, expenses, bills, frames, members, items, _tables = await _load(db, club["id"])
    payments = _payments_in(logs, ym)
    income_rows = _income_rows(payments)
    totals, counts = _source_totals(income_rows)
    income_total = money(sum(totals.values()))

    month_expenses = [e for e in expenses if month_of(e.get("date", "")) == ym]
    by_cat, cat_count = {}, {}
    for e in month_expenses:
        by_cat[e["category"]] = money(by_cat.get(e["category"], 0) + e["amount"])
        cat_count[e["category"]] = cat_count.get(e["category"], 0) + 1
    expense_total = money(sum(e["amount"] for e in month_expenses))

    # ---- stock profit (accrual): counter bills + frame-attached items ----
    stock_map = {}
    def feed(list_items):
        for li in list_items:
            name = li.get("name", "?")
            row = stock_map.setdefault(name, {
                "name": name, "itemId": li.get("menuItemId") or "",
                "category": li.get("category", ""), "qtySold": 0,
                "revenue": 0.0, "cost": 0.0, "profit": 0.0})
            row["qtySold"] += li.get("qty", 0)
            row["revenue"] += li.get("amount", 0)
            row["cost"] += li.get("costAmount", 0)
    cat_of = {i["id"]: i.get("category", "") for i in items}
    for b in bills:
        if ist_month(b["createdAt"]) != ym:
            continue
        for li in b.get("items") or []:
            li = dict(li); li["category"] = cat_of.get(li.get("menuItemId"), "Cafe")
            feed([li])
    for f in frames:
        if ist_month(f["createdAt"]) != ym:
            continue
        for li in f.get("items") or []:
            li = dict(li); li["category"] = cat_of.get(li.get("menuItemId"), "Cafe")
            feed([li])
    stock_rows = sorted(stock_map.values(), key=lambda r: -r["revenue"])
    for r in stock_rows:
        r["profit"] = money(r["revenue"] - r["cost"])
        r["revenue"] = money(r["revenue"]); r["cost"] = money(r["cost"])
        r["cogs"] = r["cost"]  # web alias (Finance screen)
    stock_totals = {
        "qtySold": sum(r["qtySold"] for r in stock_rows),
        "revenue": money(sum(r["revenue"] for r in stock_rows)),
        "cost": money(sum(r["cost"] for r in stock_rows)),
        "profit": money(sum(r["profit"] for r in stock_rows)),
    }

    # ---- balance sheet ----
    receivables = money(sum((m.get("dueAmount") or 0) for m in members))
    inventory = money(sum((i.get("stockQty") or 0) * (i.get("costPrice") or 0)
                          for i in items))
    wallets = money(sum(max(0.0, m.get("walletBalance") or 0) for m in members))
    assets = money(receivables + inventory)
    balance = {
        "receivables": receivables, "inventory": inventory, "assets": assets,
        "wallets": wallets, "liabilities": wallets,
        "netPosition": money(assets - wallets),
    }

    resp = {
        "month": ym,
        "income": {**totals, "total": income_total,
                   "dueCollections": totals["due"],  # web P&L row
                   "counts": {**counts,
                              "itemBills": counts.get("items", 0),      # web alias
                              "duePayments": counts.get("due", 0)}},    # web alias
        "expenses": {"total": expense_total, "byCategory": by_cat, "rows": month_expenses},
        "pnl": {"incomeTotal": income_total, "expenseTotal": expense_total,
                "netProfit": money(income_total - expense_total)},
        "stockProfit": {"rows": stock_rows, "totals": stock_totals},
        "balance": balance,
        "daily": _daily(income_rows, expenses, ym),
        # ---- web frontend aliases (Flutter extra keys ignore karta hai) ----
        "stock": {                                  # web: fin.stock.*
            "items": stock_rows,                    # rows me itemId bhi hai ab
            "totalRevenue": stock_totals["revenue"],
            "totalProfit": stock_totals["profit"],
            "totalCogs": stock_totals["cost"],
            "totalQtySold": stock_totals["qtySold"],
        },
        "balanceSheet": {                           # web: fin.balanceSheet.*
            "assets": {"receivables": receivables, "inventory": inventory, "total": assets},
            "liabilities": {"memberWallets": wallets, "total": wallets},
            "netPosition": balance["netPosition"],
        },
        "expenseCategories": [                      # web: array form of byCategory (map)
            {"category": k, "amount": v, "count": cat_count.get(k, 0)}
            for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])
        ],
    }
    if wants_web(request):  # web maps expenses.byCategory as an ARRAY
        resp["expenses"]["byCategory"] = resp["expenseCategories"]
    return resp


@router.get("/day-close")
async def day_close(request: Request, club_id: str, date: str = "", user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    day = date or today_ist()
    if not DATE_RE.match(day):
        raise HTTPException(400, "Date must look like YYYY-MM-DD")
    db = await db_mod.get_db()
    logs, expenses, bills, frames, members, *_ = await _load(db, club["id"])
    payments = [l for l in logs if ist_date(l["createdAt"]) == day]
    income_rows = _income_rows(payments)
    by_mode, by_source, counts = {}, {}, {}
    for l in income_rows:
        mode = l.get("mode", "cash")
        by_mode[mode] = money(by_mode.get(mode, 0) + l.get("amount", 0))
        src = l.get("type", "frames")
        if src not in ("frames", "items", "memberships", "due", "tournaments"):
            src = "frames"
        by_source[src] = money(by_source.get(src, 0) + l.get("amount", 0))
        counts[src] = counts.get(src, 0) + 1
    if "due" in by_source:  # web aliases (explicit key-lookup UIs)
        by_source["duePayments"] = by_source["due"]
        by_source["dueCollections"] = by_source["due"]
    day_expenses = [e for e in expenses if e.get("date") == day]
    exp_total = money(sum(e["amount"] for e in day_expenses))
    exp_by_cat = {}
    for e in day_expenses:
        exp_by_cat[e["category"]] = money(exp_by_cat.get(e["category"], 0) + e["amount"])

    day_frames = [f for f in frames if ist_date(f["createdAt"]) == day]
    day_bills = [b for b in bills if ist_date(b["createdAt"]) == day]
    live_sessions = await db.sessions.count_documents({"clubId": club["id"]})

    item_map = {}
    for b in day_bills:
        for li in b.get("items") or []:
            row = item_map.setdefault(li["name"], {"name": li["name"], "qty": 0,
                                                   "revenue": 0.0, "profit": 0.0})
            row["qty"] += li.get("qty", 0)
            row["revenue"] += li.get("amount", 0)
            row["profit"] += li.get("amount", 0) - li.get("costAmount", 0)
    for r in item_map.values():
        r["revenue"] = money(r["revenue"]); r["profit"] = money(r["profit"])
    top_items = sorted(item_map.values(), key=lambda r: -r["revenue"])[:5]

    cash_collected = money(by_mode.get("cash", 0))
    drawer = money(cash_collected - exp_total)
    collected = money(sum(by_mode.values()))
    pending_due = money(sum((m.get("dueAmount") or 0) for m in members))
    resp = {
        "date": day, "clubName": club.get("name", ""),
        "collected": collected, "net": money(collected - exp_total),
        "totalDueNow": pending_due,
        "byMode": by_mode, "bySource": by_source, "sourceCounts": counts,
        "counts": {  # web alias (Day Close screen: counts.payments …)
            "payments": len(income_rows), "frames": counts.get("frames", 0),
            "itemBills": len(day_bills), "memberships": counts.get("memberships", 0),
            "duePayments": counts.get("due", 0),
            "tournaments": counts.get("tournaments", 0)},
        "frames": {  # web alias
            "count": len(day_frames),
            "tableAmount": money(sum(f.get("tableAmount", 0) for f in day_frames)),
            "itemsAmount": money(sum(f.get("itemsAmount", 0) for f in day_frames))},
        "liveSessions": live_sessions,
        "expenses": {"total": exp_total, "count": len(day_expenses),
                     "byCategory": exp_by_cat, "rows": day_expenses},
        "expenseCategories": [  # web alias — byCategory dict ka array form
            {"category": k, "amount": v}
            for k, v in sorted(exp_by_cat.items(), key=lambda kv: -kv[1])],
        "ops": {
            "framesBilled": len(day_frames),
            "tableRevenue": money(sum(f.get("tableAmount", 0) for f in day_frames)),
            "itemsRevenue": money(sum(b.get("amount", 0) for b in day_bills)),
            "itemBills": len(day_bills),
            "liveTables": live_sessions,
        },
        "topItems": top_items,
        "closing": {
            "cashCollected": cash_collected, "expenses": exp_total,
            "drawerCash": drawer, "upi": by_mode.get("upi", 0),
            "card": by_mode.get("card", 0),
        },
        "pendingDue": pending_due,
    }
    if wants_web(request):  # web maps expenses.byCategory as an ARRAY
        resp["expenses"]["byCategory"] = resp["expenseCategories"]
    return resp


@router.get("/utilisation")
async def utilisation(club_id: str, month: str = "", user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    ym = _month_or_400(month) if month else month_of(today_ist())
    db = await db_mod.get_db()
    frames = await db.frames.find({"clubId": club["id"]}).to_list(None)
    tables = await db.tables.find({"clubId": club["id"]}).to_list(None)
    name_of = {t["id"]: t["name"] for t in tables}
    per_table = {}
    histogram = {h: {"hour": h, "frames": 0, "minutes": 0} for h in range(24)}
    for f in frames:
        if ist_month(f["createdAt"]) != ym:
            continue
        row = per_table.setdefault(f["tableId"], {
            "tableId": f["tableId"], "name": f.get("tableName") or name_of.get(f["tableId"], "?"),
            "frames": 0, "minutes": 0, "revenue": 0.0})
        row["frames"] += 1
        row["minutes"] += f.get("durationMinutes", 0)
        row["revenue"] += f.get("tableAmount", 0)
        h = ist_hour(f["startedAt"])
        histogram[h]["frames"] += 1
        histogram[h]["minutes"] += f.get("durationMinutes", 0)
    rows = sorted(per_table.values(), key=lambda r: -r["revenue"])
    for r in rows:
        r["revenue"] = money(r["revenue"])
        r["effRate"] = money(r["revenue"] / (r["minutes"] / 60)) if r["minutes"] else 0.0
    totals = {
        "frames": sum(r["frames"] for r in rows),
        "minutes": sum(r["minutes"] for r in rows),
        "revenue": money(sum(r["revenue"] for r in rows)),
    }
    hours = [histogram[h] for h in range(24)]
    peak = max(hours, key=lambda h: (h["minutes"], h["frames"]), default=None)
    return {
        "month": ym, "tables": rows, "totals": totals, "hours": hours,
        "peakHour": peak["hour"] if peak and (peak["minutes"] or peak["frames"]) else None,
        "daysInMonth": days_in_month(ym),
    }
