"""Tournaments — knockout flow (busy tables, on-table timers, loser pays,
champion + prize expenses) + league round robin with standings."""
from datetime import timedelta

from app import db as db_mod
from app.util import iso, parse_iso
from conftest import add_table, check, go, master_api, owner_with_club


async def _backdate_match(tid, mid, seconds):
    db = await db_mod.get_db()
    t = await db.tournaments.find_one({"id": tid})
    for m in t["matches"]:
        if m["id"] == mid and m.get("startedAt"):
            m["startedAt"] = iso(parse_iso(m["startedAt"]) - timedelta(seconds=seconds))
    await db.tournaments.update_one({"id": tid}, {"$set": {"matches": t["matches"]}})


async def _ko_scene(api, master, n=4, entry=200):
    api, club = await owner_with_club(api, master)
    cid = club["id"]
    t1 = await add_table(api, cid, "Snooker 1", 240, 20)
    t2 = await add_table(api, cid, "Pool 1", 60, 0)
    tour = (await api.post(f"/clubs/{cid}/tournaments", {
        "name": "Friday Snooker Open", "game": "Snooker", "date": "2026-08-14",
        "entryFee": entry, "prize1": 1500, "prize2": 500, "maxPlayers": 16,
        "tableRate": 60})).json()   # ₹60/hr tournament override: 1m == ₹1
    names = ["Aarav", "Vihaan", "Kabir", "Zaid", "Omi", "Rey"][:n]
    for i, name in enumerate(names):
        r = await api.post(f"/clubs/{cid}/tournaments/{tour['id']}/participants",
                           {"name": name, "phone": f"98000000{i:02d}"})
        assert r.status_code in (200, 201), r.text
        tour = r.json()
    return cid, t1, t2, tour


def test_knockout_full_flow(api):
    async def main():
        master = await master_api()
        cid, t1, t2, tour = await _ko_scene(api, master)
        tid = tour["id"]
        check(tour["playerCount"] == 4 and tour["status"] == "upcoming", "tour created")

        # entry paid toggles -> collected (paid entries only)
        for p in tour["participants"][:3]:
            tour = (await api.patch(
                f"/clubs/{cid}/tournaments/{tid}/participants/{p['pid']}",
                {"paidEntry": True})).json()
        check(abs(tour["collected"] - 600) < 0.001, "3 paid entries = ₹600")

        # start -> seeded bracket with a final + 2 semis
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/start")).json()
        check(tour["status"] == "running", "running after start")
        matches = tour["matches"]
        labels = sorted(m["label"].split(" ·")[0] for m in matches)
        check(labels == ["Final", "Semi Final", "Semi Final"], "bracket labels")
        ready = [m for m in matches if m["status"] == "ready" and m["round"] == 0]
        check(len(ready) == 2, "two ready semis")

        # players tab locked after start
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": "Late Comer", "phone": "1"})
        check(r.status_code == 400, "no adds after start")

        # busy table -> 400 (a live session occupies table 1)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"], "players": [{"label": "G", "type": "guest"}]})
        sess = r.json()
        m1 = ready[0]
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m1['id']}/play",
                           {"tableId": t1["id"]})
        check(r.status_code == 400 and "busy" in r.json()["detail"].lower(),
              "session-occupied table -> 400")
        await api.delete(f"/clubs/{cid}/sessions/{sess['id']}")

        # put both semis on tables
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m1['id']}/play",
                               {"tableId": t1["id"]})).json()
        m_live = next(m for m in tour["matches"] if m["id"] == m1["id"])
        check(m_live["status"] == "table_live" and m_live["startedAt"],
              "on-table timer running")
        # second table has a live match now -> 400 for the other semi
        m2 = ready[1]
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m2['id']}/play",
                           {"tableId": t1["id"]})
        check(r.status_code == 400, "live-match table -> 400")
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m2['id']}/play",
                               {"tableId": t2["id"]})).json()

        # semi 1 result: 1 minute on table -> ₹1 (tournament rate override beats min ₹20!)
        await _backdate_match(tid, m1["id"], 40)
        winner1 = m1["p1"]["pid"]
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m1['id']}/result",
                               {"score1": 2, "score2": 0, "winnerPid": winner1})).json()
        m1a = next(m for m in tour["matches"] if m["id"] == m1["id"])
        check(m1a["status"] == "done" and abs(m1a["tableAmount"] - 1) < 0.001,
              "₹60/hr x 1m = ₹1 (override, no min charge)")
        check(abs(tour["tableCharges"] - 1) < 0.001, "tableCharges += 1")
        final = next(m for m in tour["matches"] if m["round"] == 1)
        check(final["p1"] and final["p1"]["pid"] == winner1, "winner auto-advances")

        # semi 2 by SCORE-ONLY (never went on a table) — no charge
        m2 = ready[1]
        winner2 = m2["p2"]["pid"]
        # m2 is table_live in this flow — cancel that by result on table instead:
        # actually it IS on table 2; give it a 30-min game (₹60/hr -> ₹30)
        await _backdate_match(tid, m2["id"], 1790)
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{m2['id']}/result",
                               {"score1": 1, "score2": 3, "winnerPid": winner2})).json()
        m2a = next(m for m in tour["matches"] if m["id"] == m2["id"])
        check(abs(m2a["tableAmount"] - 30) < 0.001, "30m @ ₹60/hr = ₹30")
        final = next(m for m in tour["matches"] if m["round"] == 1)
        check(final["status"] == "ready" and final["p2"]["pid"] == winner2,
              "final now ready")

        # final: score-only, no timer -> no table charge
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{final['id']}/result",
                               {"score1": 3, "score2": 2, "winnerPid": winner1})).json()
        check(tour["status"] == "completed", "completed after final")
        champ_name = next(p["name"] for p in tour["participants"]
                          if p["pid"] == winner1)
        check(tour["winnerName"] == champ_name, "champion banner data")
        check(tour["completedAt"], "completedAt stamped")

        # prizes auto-expensed under tournament category
        expenses = (await api.get(f"/clubs/{cid}/expenses")).json()["rows"]
        prizes = [e for e in expenses if e["category"] == "tournament"]
        check(abs(sum(e["amount"] for e in prizes) - 2000) < 0.001,
              "prize1+prize2 auto-expensed (1500+500)")

        # tournaments = 5th income bucket in the monthly sheet
        rep = (await api.get(f"/clubs/{cid}/reports/monthly")).json()
        check(abs(rep["sourceTotals"]["tournaments"] - (600 + 31)) < 0.001,
              "entries 600 + table charges 31 in monthly")

        # completed can't be cancelled
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/cancel")
        check(r.status_code == 400, "completed can't cancel")
        await master.close()
    go(main())


def test_knockout_byes(api):
    async def main():
        master = await master_api()
        cid, t1, t2, tour = await _ko_scene(api, master, n=3)
        tid = tour["id"]
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/start")).json()
        matches = tour["matches"]
        byes = [m for m in matches if m["status"] == "bye"]
        check(len(byes) == 1, "3 players -> 1 bye (4-size bracket)")
        final = next(m for m in matches if m["round"] == 1)
        check(final["p1"] or final["p2"], "bye advanced to the final automatically")
        await master.close()
    go(main())


def test_league_round_robin_and_standings(api):
    async def main():
        master = await master_api()
        cid, t1, t2, tour = await _ko_scene(api, master, n=3, entry=0)
        tid = tour["id"]
        # convert this tour to league is not possible after create — make a league one
        tour = (await api.post(f"/clubs/{cid}/tournaments", {
            "name": "Sunday Pool League", "game": "Pool", "date": "2026-08-16",
            "entryFee": 100, "prize1": 800, "prize2": 0, "maxPlayers": 16,
            "format": "league"})).json()
        tid = tour["id"]
        for i, name in enumerate(["Meera", "Dhruv", "Sana"]):
            await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": name, "phone": f"98111111{i:02d}"})
        tour = (await api.post(f"/clubs/{cid}/tournaments/{tid}/start")).json()
        matches = tour["matches"]
        # 3 players -> ghost -> 3 rounds x 2 fixtures (1 real + 1 bye) = 6
        check(len(matches) == 6, "circle method fixtures")
        check(len([m for m in matches if m["status"] == "bye"]) == 3, "3 ghost byes")
        real = [m for m in matches if m["status"] == "ready"]
        check(len(real) == 3, "3 real league fixtures")

        # play all score-only: Meera beats both, Dhruv beats Sana
        results = {}
        for m in [dict(x) for x in real]:
            p1, p2 = m["p1"], m["p2"]
            for side in (p1, p2):
                if side["name"] in ("Meera", "Dhruv", "Sana"):
                    pass
            if "Meera" in (p1["name"], p2["name"]):
                winner = p1 if p1["name"] == "Meera" else p2
            elif "Dhruv" in (p1["name"], p2["name"]):
                winner = p1 if p1["name"] == "Dhruv" else p2
            else:
                winner = p1
            s1, s2 = (2, 0) if winner["pid"] == p1["pid"] else (0, 2)
            tour = (await api.post(
                f"/clubs/{cid}/tournaments/{tid}/matches/{m['id']}/result",
                {"score1": s1, "score2": s2, "winnerPid": winner["pid"]})).json()
        check(tour["status"] == "completed", "league auto-completes when settled")
        standings = tour["_standings"]
        check(standings[0]["name"] == "Meera" and standings[0]["points"] == 6,
              "Meera champion with 6 pts")
        check(tour["winnerName"] == "Meera" and tour["runnerUpName"] == "Dhruv",
              "champion + runner-up from standings")
        expenses = (await api.get(f"/clubs/{cid}/expenses")).json()["rows"]
        check(any(e["category"] == "tournament" and abs(e["amount"] - 800) < 0.001
                  for e in expenses), "league prize auto-expensed")
        # format locked after creation
        r = await api.patch(f"/clubs/{cid}/tournaments/{tid}", {"name": "Renamed"})
        check(r.status_code == 200, "name patch ok")
        await master.close()
    go(main())
