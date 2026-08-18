"""Web (React) response-shape aliases — strictly ADDITIVE keys; Flutter canonical
keys (byCategory dict, sourceTotals.due, handlers, accounts…) must stay intact."""
from datetime import timedelta

from app import db as db_mod
from app.util import iso, parse_iso, today_ist
from conftest import (add_member, add_table, check, go, master_api,
                      owner_with_club)


async def _scene(api, master):
    """1 frame (₹60 cash, Imran pays) · chai item + restock (auto expense) ·
    1 cash item-bill (₹120) · 1 manual expense (note:null, no date — web sends these)."""
    api, club = await owner_with_club(api, master)
    cid = club["id"]
    t = await add_table(api, cid, "Snooker 1", 60, 0)
    ravi = await add_member(api, cid, "Ravi")
    imran = await add_member(api, cid, "Imran")
    r = await api.post(f"/clubs/{cid}/sessions", {
        "tableId": t["id"],
        "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                    {"label": "Imran", "type": "member", "memberId": imran["id"]}]})
    s = r.json()
    db = await db_mod.get_db()
    start = parse_iso(s["startedAt"]) - timedelta(seconds=3590)
    await db.sessions.update_one({"id": s["id"]}, {"$set": {"startedAt": iso(start)}})
    await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
    ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
    r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                       {"winners": [ravi_pid], "cashPaid": 60, "mode": "cash"})
    assert r.status_code == 200, r.text

    item = (await api.post(f"/clubs/{cid}/menu-items", {
        "name": "Chai", "category": "Cafe", "price": 60, "costPrice": 25,
        "stockQty": 5})).json()
    r = await api.post(f"/clubs/{cid}/menu-items/{item['id']}/restock",
                       {"qty": 10, "unitCost": 25})
    assert r.status_code == 200, r.text
    r = await api.post(f"/clubs/{cid}/item-bills",
                       {"items": [{"menuItemId": item["id"], "qty": 2}],
                        "customerName": "Walk-in", "mode": "cash"})
    assert r.status_code in (200, 201), r.text

    r = await api.post(f"/clubs/{cid}/expenses",
                       {"title": "Rent", "category": "rent", "amount": 1000,
                        "note": None})  # web note:null + no date
    assert r.status_code == 201, r.text
    assert r.json()["date"] == today_ist() and r.json()["note"] == "", \
        "note null + missing date normalised"
    return cid, t, ravi, imran, item


def test_day_close_web_aliases(api):
    async def main():
        master = await master_api()
        cid, *_ = await _scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/day-close")
        check(r.status_code == 200, "day-close 200")
        d = r.json()
        c = d["counts"]
        check({"payments", "frames", "itemBills", "memberships", "duePayments",
               "tournaments"} <= set(c), "counts{payments,frames,itemBills,…}")
        check(c["frames"] == 1 and c["itemBills"] == 1, "counts values")
        check(d["frames"]["count"] == 1 and d["frames"]["tableAmount"] == 60,
              "frames{count,tableAmount,itemsAmount}")
        check(d.get("clubName"), "clubName present")
        check(abs(d["net"] - (d["collected"] - d["expenses"]["total"])) < 0.001, "net")
        check("totalDueNow" in d and "liveSessions" in d, "totalDueNow + liveSessions")
        check(isinstance(d.get("expenseCategories"), list)
              and {x["category"] for x in d["expenseCategories"]} >= {"rent", "stock"},
              "expenseCategories array alias")
        # Flutter canonical intact
        check(isinstance(d["expenses"]["byCategory"], dict), "byCategory stays a dict")
        check(d["expenses"]["count"] == 2, "expenses count")
        check({"frames", "due"} <= set(d["sourceCounts"]) or d["sourceCounts"],
              "sourceCounts intact")
        await master.close()
    go(main())


def test_monthly_web_aliases(api):
    async def main():
        master = await master_api()
        cid, *_ = await _scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/monthly")
        d = r.json()
        check(d["counts"]["itemBills"] == d["sourceCounts"]["items"], "counts.itemBills")
        check(d["counts"]["frames"] == 1, "counts.frames")
        check(d["sourceTotals"]["dueCollections"] == d["sourceTotals"]["due"],
              "sourceTotals.dueCollections alias")
        check(d["totalEarnings"] == d["sourceTotals"]["total"], "totalEarnings")
        row = d["rows"][0]
        check(row["label"] and row["createdAt"] and "desc" in row and "date" in row,
              "rows label/createdAt added + desc/date intact")
        check(d["daily"][0]["dueCollections"] == d["daily"][0]["due"],
              "daily dueCollections alias")
        await master.close()
    go(main())


def test_finance_web_aliases(api):
    async def main():
        master = await master_api()
        cid, *_ = await _scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/finance")
        d = r.json()
        st = d["stock"]
        check(st["items"] and st["items"][0]["cogs"] == st["items"][0]["cost"],
              "stock row cogs alias")
        check(st["totalCogs"] == d["stockProfit"]["totals"]["cost"], "stock totalCogs")
        check(d["expenseCategories"] and all("count" in x for x in d["expenseCategories"]),
              "expenseCategories have count")
        check(isinstance(d["expenses"]["byCategory"], dict), "byCategory stays a dict")
        await master.close()
    go(main())


def test_expenses_web_aliases(api):
    async def main():
        master = await master_api()
        cid, *_ = await _scene(api, master)
        r = await api.get(f"/clubs/{cid}/expenses", params={"month": today_ist()[:7]})
        d = r.json()
        check(d["count"] == 2 and len(d["rows"]) == 2, "list count alias")
        stock_row = next(e for e in d["rows"] if e["category"] == "stock")
        check(stock_row["refType"] == "menu_item" and stock_row.get("refId"),
              "auto-stock refType menu_item (+refId)")
        rent_row = next(e for e in d["rows"] if e["category"] == "rent")
        check(rent_row["refType"] == "", "manual expense refType empty")
        check(isinstance(d["byCategory"], dict)
              and isinstance(d["expenseCategories"], list), "both category shapes")
        await master.close()
    go(main())


def test_team_web_aliases(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        r = await api.get("/team")
        d = r.json()
        check("clubs" in d and "rows" in d and d["rows"] == d["clubs"], "rows alias")
        row = next(x for x in d["clubs"] if x["club"]["id"] == club["id"])
        check(row["staff"] == row["handlers"], "staff alias mirrors handlers")
        check(any(h["isOwner"] for h in row["staff"]), "owner listed in staff")
        await master.close()
    go(main())


def test_master_overview_web_aliases():
    async def main():
        master = await master_api()
        r = await master.get("/master/overview")
        d = r.json()
        st = d["stats"]
        check({"totalUsers", "activeUsers", "totalClubs", "activeSubscriptions",
               "monthlyRecurringRevenue"} <= set(st), "stats{…} keys")
        check(isinstance(d["users"], list) and isinstance(d["clubs"], list),
              "users[] + clubs[] embedded")
        check(d["accounts"] == st["totalUsers"], "legacy accounts == stats.totalUsers")
        r = await master.get("/master/subscription-plans")
        check(r.status_code == 200 and isinstance(r.json(), list),
              "master plan catalog GET")
        await master.close()
    go(main())


def test_change_winner_web_alias(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, _item = await _scene(api, master)
        fr = (await api.get(f"/clubs/{cid}/frames")).json()[0]
        imran_pid = next(p["pid"] for p in fr["players"]
                         if p.get("memberId") == imran["id"])
        r = await api.patch(f"/clubs/{cid}/frames/{fr['id']}/winners",
                            {"winnerPlayerIds": [imran_pid]})  # web alias body
        check(r.status_code == 200, f"winnerPlayerIds alias patch: {r.text}")
        fr = (await api.get(f"/clubs/{cid}/frames")).json()[0]
        check(fr["winnersPids"] == [imran_pid] and fr["winners"] == ["Imran"],
              "winner actually swapped (pids + labels)")
        check(fr["settlements"]
              and all("name" in s and "playerName" in s for s in fr["settlements"]),
              "settlement lines carry name/playerName aliases")
        await master.close()
    go(main())
