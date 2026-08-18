"""Reports — monthly revenue sheet, finance P&L + balance sheet, day close,
utilisation & peak hours, stats."""
from datetime import timedelta

from app import db as db_mod
from app.util import iso, now_utc, parse_iso, today_ist
from conftest import (add_member, add_table, check, go, master_api,
                      owner_with_club)


async def _backdate(sid, seconds):
    db = await db_mod.get_db()
    s = await db.sessions.find_one({"id": sid})
    start = parse_iso(s["startedAt"]) - timedelta(seconds=seconds)
    await db.sessions.update_one({"id": sid}, {"$set": {"startedAt": iso(start)}})


async def _report_scene(api, master):
    """Fixtures: frame ₹60 cash + membership ₹500 upi + due ₹120 cash + expense ₹1000."""
    api, club = await owner_with_club(api, master)
    cid = club["id"]
    t = await add_table(api, cid, "Snooker 1", 60, 0)
    ravi = await add_member(api, cid, "Ravi")
    imran = await add_member(api, cid, "Imran")

    # frame: 60 min @ ₹60/hr = ₹60 cash, Imran pays
    r = await api.post(f"/clubs/{cid}/sessions", {
        "tableId": t["id"],
        "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                    {"label": "Imran", "type": "member", "memberId": imran["id"]}]})
    s = r.json()
    await _backdate(s["id"], 3590)
    await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
    ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
    r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                       {"winners": [ravi_pid], "cashPaid": 60, "mode": "cash"})
    assert r.status_code == 200, r.text

    # membership sale ₹500 (wallet plan value 500)
    plan = (await api.post(f"/clubs/{cid}/plans", {
        "name": "Gold Wallet", "type": "wallet", "amount": 500, "value": 500})).json()
    await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                   {"memberId": ravi["id"], "mode": "upi"})

    # due: Imran owes 120 via due item bill, then collects ₹120 cash
    item = (await api.post(f"/clubs/{cid}/menu-items", {
        "name": "Chai", "category": "Cafe", "price": 60, "costPrice": 25,
        "stockQty": 5})).json()
    r = await api.post(f"/clubs/{cid}/item-bills", {
        "items": [{"menuItemId": item["id"], "qty": 2}],
        "memberId": imran["id"], "mode": "due"})
    await api.post(f"/clubs/{cid}/members/{imran['id']}/payments",
                   {"amount": 120, "mode": "cash"})

    # expense ₹1000 rent today
    await api.post(f"/clubs/{cid}/expenses", {
        "title": "Rent", "category": "rent", "amount": 1000, "date": today_ist()})
    return cid, t, ravi, imran, item


def test_monthly_report(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, item = await _report_scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/monthly")
        check(r.status_code == 200, "monthly 200")
        rep = r.json()
        st = rep["sourceTotals"]
        check(abs(st["frames"] - 60) < 0.001, "frames bucket ₹60")
        check(abs(st["memberships"] - 500) < 0.001, "memberships bucket ₹500")
        check(abs(st["due"] - 120) < 0.001, "due bucket ₹120")
        check(abs(st["total"] - 680) < 0.001, "monthly total ₹680")
        check(rep["daily"] and rep["daily"][0]["total"] == 680, "daily totals")
        check(len(rep["rows"]) == 3, "3 payment rows")
        check({r["source"] for r in rep["rows"]} == {"frames", "memberships", "due"},
              "source badges")
        r = await api.get(f"/clubs/{cid}/reports/monthly", params={"month": "2099-13"})
        check(r.status_code == 400, "bad month -> 400")
        r = await api.get(f"/clubs/{cid}/reports/monthly", params={"month": "2030-01"})
        check(r.json()["sourceTotals"]["total"] == 0, "empty month ok")
        await master.close()
    go(main())


def test_finance_report(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, item = await _report_scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/finance")
        rep = r.json()
        check(abs(rep["pnl"]["incomeTotal"] - 680) < 0.001, "finance income 680")
        check(abs(rep["pnl"]["expenseTotal"] - 1000) < 0.001, "finance expenses 1000")
        check(abs(rep["pnl"]["netProfit"] - (-320)) < 0.001, "net = −320")
        bal = rep["balance"]
        check(abs(bal["wallets"] - 500) < 0.001, "wallet liability 500")
        check(abs(bal["inventory"] - 3 * 25) < 0.001, "inventory = 3 chai left × ₹25")
        check(abs(bal["receivables"] - 0) < 0.001, "receivables cleared")
        check(abs(bal["netPosition"] - (75 - 500)) < 0.001, "net position −425")
        sp = rep["stockProfit"]["totals"]
        check(sp["qtySold"] == 2 and abs(sp["revenue"] - 120) < 0.001
              and abs(sp["cost"] - 50) < 0.001, "stock profit math")
        daily = rep["daily"]
        check(daily and "running" in daily[0], "running balance present")
        await master.close()
    go(main())


def test_day_close(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, item = await _report_scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/day-close")
        check(r.status_code == 200, "day close 200")
        dc = r.json()
        check(abs(dc["collected"] - 680) < 0.001, "collected 680")
        check(abs(dc["byMode"]["cash"] - 180) < 0.001, "cash 60+120")
        check(abs(dc["byMode"]["upi"] - 500) < 0.001, "upi 500")
        check(abs(dc["bySource"]["frames"] - 60) < 0.001, "frames source")
        check(dc["ops"]["framesBilled"] == 1, "one frame billed")
        closing = dc["closing"]
        check(abs(closing["drawerCash"] - (180 - 1000)) < 0.001,
              "drawer cash = cash collected − expenses")
        r = await api.get(f"/clubs/{cid}/reports/day-close", params={"date": "08-08-2026"})
        check(r.status_code == 400, "bad date -> 400")
        await master.close()
    go(main())


def test_utilisation(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, item = await _report_scene(api, master)
        r = await api.get(f"/clubs/{cid}/reports/utilisation")
        rep = r.json()
        row = next(x for x in rep["tables"] if x["tableId"] == t["id"])
        check(row["frames"] == 1 and row["minutes"] == 60, "frames/minutes per table")
        check(abs(row["revenue"] - 60) < 0.001 and abs(row["effRate"] - 60) < 0.001,
              "revenue + effective ₹/hr")
        check(abs(rep["totals"]["revenue"] - 60) < 0.001, "totals")
        check(rep["peakHour"] is not None and 0 <= rep["peakHour"] <= 23, "peak hour")
        check(len(rep["hours"]) == 24, "24h histogram")
        await master.close()
    go(main())


def test_stats_today_earnings(api):
    async def main():
        master = await master_api()
        cid, t, ravi, imran, item = await _report_scene(api, master)
        stats = (await api.get(f"/clubs/{cid}/stats")).json()
        check(abs(stats["todayEarnings"] - 680) < 0.001, "todayEarnings 680")
        check(abs(stats["totalDue"] - 0) < 0.001, "totalDue cleared")
        check(stats["runningSessions"] == 0, "no running sessions")
        await master.close()
    go(main())
