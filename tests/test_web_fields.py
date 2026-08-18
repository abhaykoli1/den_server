"""Web-field compatibility — Abhay ke React app ke EXACT bodies:
flat table rate, plan-on-join credit, set-balance aliases, optional phone/notes,
master starter club, aur ?client=web dual-shapes (Flutter canonical intact)."""
from conftest import (Api, add_member, add_table, check, dev_login, go,
                      master_api, owner_with_club)
from app.util import today_ist


def test_master_starter_club_on_login(api):
    async def main():
        fresh = Api()
        master = await dev_login(fresh, "master@rowdys.dev", "Boss")
        check(master.get("role") == "master", "dev-login as master email")
        check(master.get("clubIds"), "starter club attached to user")
        me = await fresh.get("/auth/me")
        r = await fresh.get("/clubs")
        owned = [c for c in r.json() if c.get("ownerUserId") == me.json()["id"]]
        check(len(owned) == 1 and owned[0]["name"] == "Rowdy's Den",
              "exactly 1 starter club auto-created")
        await fresh.get("/clubs")  # idempotent — no duplicate on second fetch
        owned2 = [c for c in r.json() if c.get("ownerUserId") == me.json()["id"]]
        check(len(owned2) == 1, "no duplicate club")
        await fresh.close()
    go(main())


def test_member_plan_credit_on_join(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        wallet = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Gold Wallet", "type": "wallet", "amount": 500, "value": 600,
            "days": 30})).json()
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Ravi", "planId": wallet["id"]})  # no phone/notes
        check(r.status_code == 201, f"member create with plan, no phone/notes: {r.text}")
        m = r.json()
        check(m["walletBalance"] == 600 and m["planName"] == "Gold Wallet"
              and m["planType"] == "wallet" and m.get("planExpiresAt"),
              "wallet plan credited + expiry set")
        # web-style pass plan — frames inside `value`
        p = (await api.post(f"/clubs/{cid}/plans", {
            "name": "10 Frame Pass", "type": "pass", "amount": 1500, "value": 10,
            "days": 60})).json()
        check(p["frames"] == 10, "pass frames mapped from value")
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Imran", "planId": p["id"], "phone": None,
                            "email": None, "notes": None})
        check(r.status_code == 201 and r.json()["passFramesLeft"] == 10,
              f"pass frames on join: {r.text}")
        check(r.json()["phone"] == "" and r.json()["notes"] == "",
              "nulls normalised to empty strings")
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Ghost", "planId": "plan_nope"})
        check(r.status_code == 404, "unknown plan -> 404")
        await master.close()
    go(main())


def test_member_plan_payment_booking(api):
    """Join-sell-reconcile — membership ka paisa day-close/monthly/finance me sync."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        wallet = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Gold Wallet", "type": "wallet", "amount": 500, "value": 600,
            "days": 30})).json()
        today = today_ist()
        dc_url = f"/clubs/{cid}/reports/day-close?date={today}"
        # 1) join with plan (default planPaid) → benefits + sale + PAYMENT log
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Payer", "planId": wallet["id"], "mode": "upi"})
        check(r.status_code == 201 and r.json()["walletBalance"] == 600,
              f"join benefits intact: {r.text}")
        payer = r.json()
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 1 and dc["bySource"]["memberships"] == 500,
              f"day-close membership counted: {dc.get('bySource')}")
        check(dc["byMode"]["upi"] == 500, f"mode-wise upi counted: {dc.get('byMode')}")
        mon = (await api.get(
            f"/clubs/{cid}/reports/monthly?month={today[:7]}")).json()
        check(mon["sourceTotals"]["memberships"] == 500
              and mon["counts"]["memberships"] == 1, "monthly sheet memberships")
        fin = (await api.get(
            f"/clubs/{cid}/reports/finance?month={today[:7]}")).json()
        check(fin["income"]["memberships"] == 500
              and fin["pnl"]["incomeTotal"] == 500, "finance P&L memberships")
        # 2) planPaid False → sirf benefits; paisa baad me /plan-payment se
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Later", "planId": wallet["id"], "planPaid": False})
        check(r.status_code == 201 and r.json()["walletBalance"] == 600,
              "unpaid join — benefits only, no payment")
        later = r.json()
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 1, "no payment booked for unpaid join")
        r = await api.post(f"/clubs/{cid}/members/{later['id']}/plan-payment", {})
        check(r.status_code == 200 and r.json()["sale"]["amount"] == 500,
              f"late plan payment booked: {r.text}")
        check(r.json()["member"]["walletBalance"] == 600,
              "reconcile does NOT re-credit benefits")
        r = await api.post(f"/clubs/{cid}/members/{later['id']}/plan-payment", {})
        check(r.status_code == 400, "idempotent — second booking blocked")
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 2
              and dc["bySource"]["memberships"] == 1000, "reconcile counted exactly once")
        # 3) member PATCH with a DIFFERENT plan = sell + book now
        monthly = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Monthly Gold", "type": "monthly", "amount": 999,
            "tableDiscountPercent": 10, "days": 30})).json()
        r = await api.patch(f"/clubs/{cid}/members/{payer['id']}",
                            {"planId": monthly["id"]})
        check(r.status_code == 200 and r.json()["planName"] == "Monthly Gold"
              and r.json()["tableDiscountPercent"] == 10,
              f"PATCH sells different plan: {r.text}")
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 3
              and dc["bySource"]["memberships"] == 1999, "edit-sell counted")
        r = await api.patch(f"/clubs/{cid}/members/{payer['id']}",
                            {"planId": monthly["id"]})  # same id echo
        check(r.status_code == 400 and "Nothing" in r.text, "same-plan echo → no-op 400")
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 3, "no double charge on echo")
        # 4) Flutter canonical — plain create untouched
        r = await api.post(f"/clubs/{cid}/members", {"name": "Plain"})
        check(r.status_code == 201 and r.json()["planId"] is None, "plain create intact")
        dc = (await api.get(dc_url)).json()
        check(dc["counts"]["memberships"] == 3, "plain member — no phantom payment")
        await master.close()
    go(main())


def test_member_set_balance_aliases(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        m = await add_member(api, cid, "Setu")
        r = await api.patch(f"/clubs/{cid}/members/{m['id']}", {"setBalance": 1200})
        check(r.status_code == 200 and r.json()["walletBalance"] == 1200,
              f"setBalance alias: {r.text}")
        r = await api.patch(f"/clubs/{cid}/members/{m['id']}", {"balance": 700})
        check(r.status_code == 200 and r.json()["walletBalance"] == 700,
              "balance alias")
        r = await api.patch(f"/clubs/{cid}/members/{m['id']}",
                            {"walletBalance": 900, "dueAmount": 50, "passFramesLeft": 3})
        check(r.json()["walletBalance"] == 900 and r.json()["dueAmount"] == 50
              and r.json()["passFramesLeft"] == 3, "direct numeric sets")
        r = await api.patch(f"/clubs/{cid}/members/{m['id']}", {"notes": None})
        check(r.status_code == 400 and "Nothing to update" in r.json()["detail"],
              "null-only patch still 400s cleanly")
        await master.close()
    go(main())


def test_table_web_flat_body(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        # EXACT web TableModal create body (flat, no nested `rate`)
        r = await api.post(f"/clubs/{cid}/tables", {
            "name": "Snooker 2", "hourlyRate": 240,
            "ratesByPlayers": {"2": 280, "3": 300}, "minCharge": 20,
            "sortOrder": 0, "active": True, "glovePrice": 0,
            "peakHourlyRate": 350, "peakStartHour": 18, "peakEndHour": 23})
        check(r.status_code == 201, f"flat table create: {r.text}")
        t = r.json()
        check(t["rate"]["hourlyRate"] == 240
              and t["rate"]["ratesByPlayers"] == {"2": 280.0, "3": 300.0}
              and t["rate"]["peakHourlyRate"] == 350
              and t["rate"]["peakStartHour"] == 18, "rate normalised + stored")
        # EXACT web PATCH: same flat shape, glove on, peak still on
        r = await api.patch(f"/clubs/{cid}/tables/{t['id']}", {
            "name": "Snooker 2", "hourlyRate": 240,
            "ratesByPlayers": {"2": 280}, "minCharge": 20, "sortOrder": 1,
            "active": True, "glovePrice": 30,
            "peakHourlyRate": 350, "peakStartHour": 18, "peakEndHour": 23})
        check(r.status_code == 200 and r.json()["sortOrder"] == 1, "flat patch ok")
        # peak OFF via explicit nulls (web sends null to disable)
        r = await api.patch(f"/clubs/{cid}/tables/{t['id']}", {
            "name": "Snooker 2", "hourlyRate": 240, "ratesByPlayers": {},
            "minCharge": 20, "sortOrder": 1, "active": True, "glovePrice": 30,
            "peakHourlyRate": None, "peakStartHour": None, "peakEndHour": None})
        check(r.json()["rate"]["peakHourlyRate"] is None
              and r.json()["rate"]["glovePrice"] == 30, "peak cleared via null")
        # Flutter canonical nested shape still works
        r = await api.post(f"/clubs/{cid}/tables", {
            "name": "Pool", "rate": {"hourlyRate": 100, "minCharge": 10}})
        check(r.status_code == 201, "nested rate create still fine")
        await master.close()
    go(main())


def test_tournament_participant_no_phone(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        r = await api.post(f"/clubs/{cid}/tournaments",
                           {"name": "Friday", "date": today_ist(), "entryFee": 200,
                            "notes": None})
        check(r.status_code == 201, f"tournament create: {r.text}")
        tid = r.json()["id"]
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": "Walk-in", "phone": None})
        parts = r.json()["participants"]
        check(r.status_code == 201 and parts and parts[0]["phone"] == "",
              f"participant without phone: {r.text}")
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": "Phone Wala", "phone": "98xxxxxx01"})
        check(r.status_code == 201, "junk phone also accepted (no validation)")
        await master.close()
    go(main())


def test_player_id_web_alias_and_multi_winner(api):
    """Web winner-selection bug: players pe `id` (web alias = pid) hona chahiye —
    warna same-name members pe ek click se SAB select ho jate the. Plus solo me
    multiple winners allowed (par sab winner nahi)."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        t = await add_table(api, cid, "Snooker 1", 60, 0)
        ravi = await add_member(api, cid, "Abhay")
        imran = await add_member(api, cid, "Abhay")  # same-name members!
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"],
            "players": [{"label": "Abhay", "type": "member", "memberId": ravi["id"]},
                        {"label": "Abhay", "type": "member", "memberId": imran["id"]},
                        {"label": "Guest", "type": "guest"}]})
        check(r.status_code == 201, f"session create: {r.text}")
        s = r.json()
        pids = [p["pid"] for p in s["players"]]
        check(all(p.get("id") == p["pid"] for p in s["players"]),
              "players carry id == pid (web alias)")
        check(len(set(pids)) == 3, "same-name players ke pids alag-alag")
        # same member do seats pe = 400 (old backend contract)
        t_dup = await add_table(api, cid, "Dup", 60, 0)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t_dup["id"],
            "players": [{"label": "Abhay", "type": "member", "memberId": ravi["id"]},
                        {"label": "Abhay", "type": "member", "memberId": ravi["id"]}]})
        check(r.status_code == 400 and "two seats" in r.json()["detail"],
              "duplicate member seats blocked")
        # club_data feed bhi aliased ho
        d = (await api.get(f"/clubs/{cid}/data")).json()
        live = next(x for x in d["sessions"] if x["id"] == s["id"])
        check(all(p.get("id") == p["pid"] for p in live["players"]),
              "club_data sessions aliased too")
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        # solo me DO winners select — teesra (guest) poore paise bhare
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [pids[0], pids[1]], "cashPaid": 60, "mode": "cash"})
        check(r.status_code == 200, f"multi-winner confirm: {r.text}")
        fr = r.json()["frame"]
        check(fr["winnersPids"] == [pids[0], pids[1]] and len(fr["losers"]) == 1,
              "2 winners + 1 payer")
        check(all(p.get("id") == p["pid"] for p in fr["players"]),
              "frame players aliased in confirm response")
        # legacy frame (bina id) — GET /frames read-time backfill
        frame_doc = dict(fr)
        for p in frame_doc["players"]:
            p.pop("id", None)
        db = await __import__("app.db", fromlist=["get_db"]).get_db()
        await db.frames.update_one({"id": fr["id"]},
                                   {"$set": {"players": frame_doc["players"]}})
        g = (await api.get(f"/clubs/{cid}/frames")).json()[0]
        check(all(p.get("id") == p["pid"] for p in g["players"]),
              "legacy frames bhi /frames list me aliased")
        # sab winner = 400 guard intact
        t2 = await add_table(api, cid, "Pool", 60, 0)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t2["id"],
            "players": [{"label": "A", "type": "guest"}, {"label": "B", "type": "guest"}]})
        s2 = r.json()
        await api.post(f"/clubs/{cid}/sessions/{s2['id']}/stop")
        r = await api.post(f"/clubs/{cid}/sessions/{s2['id']}/confirm",
                           {"winners": [p["pid"] for p in s2["players"]], "cashPaid": 60})
        check(r.status_code == 400 and "winner" in r.json()["detail"].lower(),
              "sab-winner guard intact")
        await master.close()
    go(main())


def test_confirm_bare_frame_for_web(api):
    """Web `<Confirm>` response ko bare FrameRecord use karti hai (unwrap nahi
    karti) — wants_web pe bare frame, warna canonical {frame, message}."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        t = await add_table(api, cid, "Snooker 1", 60, 0)
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Chai", "category": "Cafe", "price": 60, "costPrice": 25,
            "stockQty": 5})).json()
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"],
            "players": [{"label": "A", "type": "guest"}, {"label": "B", "type": "guest"}]})
        s = r.json()
        # mid-session item (web batch shape {items:[{itemId, qty}]})
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"items": [{"itemId": item["id"], "qty": 1}]})
        check(r.status_code in (200, 201), f"items attach: {r.text}")
        check(r.json()["items"][0]["itemId"] == item["id"]
              and r.json()["items"][0]["menuItemId"] == item["id"],
              "item line carries itemId + menuItemId")
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        pid = s["players"][0]["pid"]
        # app shape: wrapper
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [pid], "cashPaid": 500, "mode": "cash"})
        check(r.status_code == 200 and "frame" in r.json() and "message" in r.json(),
              f"canonical confirm wrapper (app): {r.text}")
        # second session → confirm AS WEB → bare frame
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"],
            "players": [{"label": "A", "type": "guest"}, {"label": "B", "type": "guest"}]})
        s2 = r.json()
        await api.post(f"/clubs/{cid}/sessions/{s2['id']}/stop")
        pid2 = s2["players"][0]["pid"]
        r = await api.post(f"/clubs/{cid}/sessions/{s2['id']}/confirm?client=web",
                           {"winnerPlayerIds": [pid2], "paidAmount": 500,
                            "paymentMode": "cash"})
        check(r.status_code == 200, f"web confirm: {r.text}")
        body = r.json()
        check("frameAmount" in body and "id" in body and "frame" not in body,
              "web confirm returns BARE frame doc")
        check(isinstance(body.get("settlements"), list), "bare frame has settlements")
        await master.close()
    go(main())


def test_tournament_web_flow(api):
    """React TournamentsScreen ka poora lifecycle: paid entries (mode ke saath),
    start → web 'pending' (canonical 'ready'), score-only result via winner slot,
    auto-champion + auto prize expenses, standings/bracket aliases, POST mark-paid."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        r = await api.post(f"/clubs/{cid}/tournaments", {
            "name": "Friday", "game": "snooker", "format": "knockout",
            "date": today_ist(), "entryFee": 200, "prize1": 500, "prize2": 200,
            "tableRate": 0, "notes": None})
        check(r.status_code == 201, f"create: {r.text}")
        tid = r.json()["id"]
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": "Abhay", "phone": None, "memberId": None,
                            "paidEntry": True, "mode": "upi"})
        check(r.status_code == 201 and r.json()["collected"] == 200,
              f"paid entry collected at add-time: {r.text}")
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants",
                           {"name": "Vivek", "phone": None, "memberId": None,
                            "paidEntry": False})
        check(r.status_code == 201, "second entry")
        p2 = r.json()["participants"][1]["pid"]
        # web act() POSTs for mark-paid → POST alias (was 405)
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/participants/{p2}",
                           {"paidEntry": True, "mode": "cash"})
        check(r.status_code == 200 and r.json()["collected"] == 400,
              f"mark-paid POST alias: {r.text}")
        # start — web sees 'pending', canonical stays 'ready'
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/start?client=web")
        check(r.status_code == 200 and r.json()["status"] == "running", f"start: {r.text}")
        wm = [m for m in r.json()["matches"] if m["status"] == "pending"]
        check(len(wm) == 1 and wm[0]["p1"] and wm[0]["p2"],
              "web sees playable 'pending' match")
        check(r.json()["bracket"] == 2, "bracket size alias")
        mid = wm[0]["id"]
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{mid}/play",
                           {"tableId": None})
        check(r.status_code == 400 and "score-only" in r.json()["detail"],
              "null tableId → friendly 400 (use score-only)")
        r = await api.get(f"/clubs/{cid}/tournaments")
        can = next(t for t in r.json() if t["id"] == tid)
        check(can["matches"][0]["status"] == "ready", "canonical 'ready' intact (app)")
        # web score-only result — winner as slot "1"/"2"
        r = await api.post(f"/clubs/{cid}/tournaments/{tid}/matches/{mid}/result?client=web",
                           {"winner": "2", "score1": 0, "score2": 2})
        check(r.status_code == 200, f"result: {r.text}")
        done = r.json()
        check(done["status"] == "completed" and done["winnerName"] == "Vivek",
              "auto-champion after final")
        check(done["matches"][0]["status"] == "played", "web sees 'played'")
        check("standings" in done and done["_standings"] == [], "standings alias")
        exp = await api.get(f"/clubs/{cid}/expenses")
        check(sum(e["amount"] for e in exp.json()["rows"]
                  if e["category"] == "tournament") == 700,
              "prize1+prize2 auto-expenses booked")
        await master.close()
    go(main())


def test_web_dual_shape_client_flag(api):
    """?client=web (ya X-Client/Origin sniff) → web shapes; default → Flutter shapes."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        await api.post(f"/clubs/{cid}/expenses",
                       {"title": "Rent", "category": "rent", "amount": 1000})
        # expenses: web → bare array
        r = await api.get(f"/clubs/{cid}/expenses", params={"client": "web"})
        check(isinstance(r.json(), list) and r.json()[0]["title"] == "Rent",
              "expenses bare array for web")
        # default (no flag) → object with rows (Flutter)
        r = await api.get(f"/clubs/{cid}/expenses")
        check(isinstance(r.json(), dict) and "rows" in r.json(),
              "expenses object for app")
        # day-close: web → byCategory array
        r = await api.get(f"/clubs/{cid}/reports/day-close", params={"client": "web"})
        d = r.json()
        check(isinstance(d["expenses"]["byCategory"], list)
              and d["expenses"]["byCategory"][0]["category"] == "rent",
              "day-close byCategory array for web")
        r = await api.get(f"/clubs/{cid}/reports/day-close")
        check(isinstance(r.json()["expenses"]["byCategory"], dict),
              "day-close byCategory dict for app")
        # finance: same dual-shape
        r = await api.get(f"/clubs/{cid}/reports/finance", params={"client": "web"})
        check(isinstance(r.json()["expenses"]["byCategory"], list),
              "finance byCategory array for web")
        r = await api.get(f"/clubs/{cid}/reports/finance")
        check(isinstance(r.json()["expenses"]["byCategory"], dict),
              "finance byCategory dict for app")
        # Origin sniff path (Vercel web) — no explicit flag
        r = await api.get(f"/clubs/{cid}/expenses",
                          headers={"Origin": "https://den-frontend-two.vercel.app"})
        check(isinstance(r.json(), list), "Origin sniff → web shape")
        await master.close()
    go(main())


def test_frame_web_aliases_and_due_harvest(api):
    """★ MONEY-LEAK fix: web FinalBillCard cashPaid = frame + losers ke OLD DUES.
    Extra cash ab "extra held" note me nahi khotaa — losers ke dues se harvest
    hota hai (due ledger entry + member.dueAmount ghattaa), aur frame doc pe
    poora web alias set (totalAmount/paidAmount/dueAmount/status/winnerPlayerIds
    /oldDue*…) write-time. Winner correction pe harvest REVERSE hota hai."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        t = await add_table(api, cid, "Snooker 1", 120, 0)  # ₹2/min
        m = (await api.post(f"/clubs/{cid}/members",
                            {"name": "Ravi", "dueAmount": 500})).json()
        check(m["dueAmount"] == 500 and "updatedAt" in m, "member opens with due")
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"], "players": [
                {"label": "Ravi", "type": "member", "memberId": m["id"]},
                {"label": "Guest B", "type": "guest"}]})
        s = r.json()
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        guest_pid = s["players"][1]["pid"]
        member_pid = s["players"][0]["pid"]
        # frame = ₹2 (1 min ceil) · cashPaid 200 → 198 harvest hona chahiye
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm?client=web",
                           {"winnerPlayerIds": [guest_pid], "paidAmount": 200,
                            "paymentMode": "cash"})
        check(r.status_code == 200, f"web confirm: {r.text}")
        f = r.json()
        check(f["frameAmount"] == 2.0, f"frame only ₹2: {f.get('frameAmount')}")
        check(f["oldDueAmount"] == 198.0 and f["oldDuePaid"] == {m["id"]: 198.0},
              f"old due harvested: {f.get('oldDuePaid')}")
        check(f["oldDueBefore"] == {m["id"]: 500.0}, "oldDueBefore snapshot")
        check(f["totalAmount"] == 200.0, "totalAmount = frame + old due")
        check(f["paidAmount"] == 200.0 and f["dueAmount"] == 0.0
              and f["status"] == "paid", "paid/due/status aliases")
        check(f["winnerPlayerIds"] == [guest_pid]
              and f["loserPlayerIds"] == [member_pid], "winner/loser pid aliases")
        check(f["paymentMode"] == "cash" and f["requestedPaymentMode"] == "cash",
              "paymentMode aliases")
        check("membershipDiscountPercent" in f and "passTableCredit" in f
              and "passFramesUsed" in f and "passMemberId" in f,
              "membership/pass alias keys present")
        line = next(x for x in f["settlements"] if x["memberId"] == m["id"])
        check(line["memberName"] == "Ravi" and line["oldDuePart"] == 198.0,
              "settlement memberName + oldDuePart")
        # member due ghat gaya
        m2 = (await api.get(f"/clubs/{cid}/members")).json()[0]
        check(m2["dueAmount"] == 302.0, f"due 500-198=302: {m2['dueAmount']}")
        # ledger: due source 198 + frames 2 (cash total 200, no double count)
        mon = (await api.get(f"/clubs/{cid}/reports/monthly")).json()
        check(mon["sourceTotals"]["frames"] == 2.0
              and mon["sourceTotals"]["due"] == 198.0,
              f"ledger split frames/due: {mon['sourceTotals']}")
        dc = (await api.get(f"/clubs/{cid}/reports/day-close")).json()
        check(dc["byMode"]["cash"] == 200.0 and dc["collected"] == 200.0,
              "day-close cash total exact, leak closed")
        # frames list pe bhi aliases (read path)
        fl = (await api.get(f"/clubs/{cid}/frames")).json()
        check(fl[0]["totalAmount"] == 200.0
              and fl[0]["settlements"][0].get("memberName"), "frames list backfill")
        # ---- winner correction: guest → member winner; dues RESTORE ----
        r = await api.patch(f"/clubs/{cid}/frames/{f['id']}/winners",
                            {"winners": [member_pid], "note": "galti ho gayi"})
        check(r.status_code == 200, f"correct: {r.text}")
        f2 = r.json()["frame"]
        m3 = (await api.get(f"/clubs/{cid}/members")).json()[0]
        check(m3["dueAmount"] == 500.0, f"harvest reversed on correction: {m3['dueAmount']}")
        check(f2["winnerPlayerIds"] == [member_pid]
              and f2["loserPlayerIds"] == [guest_pid], "correction pid aliases")
        check(f2["oldDueAmount"] == 0.0 and f2["totalAmount"] == f2["frameAmount"],
              "no harvest after winner swap (guest loser)")
        mon2 = (await api.get(f"/clubs/{cid}/reports/monthly")).json()
        check(mon2["sourceTotals"]["due"] == 0.0
              and mon2["sourceTotals"]["frames"] == 2.0,
              "due ledger entry removed by reversal, frame re-logged once")
        await master.close()
    go(main())


def test_item_bill_partial_markpaid_and_web_aliases(api):
    """Web MarkPaidModal PARTIAL {amount, mode} bhejta hai — bill partial/paid
    hota hai, member due sirf utne se ghattaa hai, ledger sirf received amount.
    Bill doc pe web aliases total/paidAmount/dueAmount/paymentMode. Delete pe
    sirf OUTSTANDING due rollback (paid part history me rehta hai)."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        m = await add_member(api, cid, "Karan")
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Red Bull", "category": "Cafe", "price": 100,
            "costPrice": 60, "stockQty": 10})).json()
        # --- due bill of ₹100 ---
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"itemId": item["id"], "qty": 1}], "memberId": m["id"],
            "mode": "due"})
        check(r.status_code == 201, f"bill: {r.text}")
        b = r.json()["bill"]
        # aliases
        check(b["total"] == 100 and b["paidAmount"] == 0 and b["dueAmount"] == 100
              and b["paymentMode"] == "due" and "updatedAt" in b,
              f"web bill aliases on create: {b}")
        check((await api.get(f"/clubs/{cid}/members")).json()[0]["dueAmount"] == 100,
              "due +100")
        # --- partial pay 40 via upi ---
        r = await api.post(f"/clubs/{cid}/item-bills/{b['id']}/mark-paid",
                           {"amount": 40, "mode": "upi"})
        check(r.status_code == 200, f"partial: {r.text}")
        b = r.json()["bill"]
        check(b["status"] == "partial" and not b["paid"]
              and b["paidAmount"] == 40 and b["dueAmount"] == 60, "partial state")
        check((await api.get(f"/clubs/{cid}/members")).json()[0]["dueAmount"] == 60,
              "member due 60")
        # --- over-pay -> 400 ---
        r = await api.post(f"/clubs/{cid}/item-bills/{b['id']}/mark-paid",
                           {"amount": 80, "mode": "cash"})
        check(r.status_code == 400 and "exceeds" in r.json()["detail"],
              f"over-pay 400: {r.text}")
        # --- settle 60 ---
        r = await api.post(f"/clubs/{cid}/item-bills/{b['id']}/mark-paid",
                           {"amount": 60, "mode": "cash"})
        b = r.json()["bill"]
        check(b["paid"] and b["status"] == "paid" and b["dueAmount"] == 0
              and b["paidAt"], f"settled: {b}")
        r = await api.post(f"/clubs/{cid}/item-bills/{b['id']}/mark-paid", {})
        check(r.status_code == 400 and "already paid" in r.json()["detail"],
              "double mark-paid 400")
        mon = (await api.get(f"/clubs/{cid}/reports/monthly")).json()
        check(mon["sourceTotals"]["items"] == 100.0,
              f"items ledger 40+60: {mon['sourceTotals']['items']}")
        # --- list_bills backfill shape ---
        lb = (await api.get(f"/clubs/{cid}/item-bills")).json()
        check(lb[0]["paymentMode"] == "due" and lb[0]["total"] == 100,
              "list aliases")
        # --- delete settled bill: no member rollback, stock restored ---
        r = await api.delete(f"/clubs/{cid}/item-bills/{b['id']}")
        check(r.status_code == 200, "delete settled")
        mm = (await api.get(f"/clubs/{cid}/members")).json()[0]
        check(mm["dueAmount"] == 0, "no due rollback for settled bill")
        # --- due bill partial 20 then delete → only outstanding 30 rolls back ---
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 1}], "memberId": m["id"],
            "mode": "due", "discount": 50})
        b2id = r.json()["bill"]["id"]
        await api.post(f"/clubs/{cid}/item-bills/{b2id}/mark-paid",
                       {"amount": 20, "mode": "cash"})
        await api.delete(f"/clubs/{cid}/item-bills/{b2id}")
        mm = (await api.get(f"/clubs/{cid}/members")).json()[0]
        check(mm["dueAmount"] == 0,
              f"only outstanding rolled back on delete: {mm['dueAmount']}")
        await master.close()
    go(main())


def test_stats_keys_and_reports_web_labels(api):
    """Web AlertsBell/Tables: stats.dueLimit/activeMembers/activeSessions/currency…
    Monthly rows: web Title-case source labels (Badge colors in se hote hain),
    Flutter canonical raw types intact. Daily rows pe dueCollections+balance,
    finance income.dueCollections + counts.itemBills/duePayments aliases."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        t = await add_table(api, cid, "Snooker 1", 60, 0)
        m = (await api.post(f"/clubs/{cid}/members",
                            {"name": "Vivek", "dueAmount": 100})).json()
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Chai", "category": "Cafe", "price": 50, "stockQty": 5})).json()
        # one frame (cash; MEMBER wins → guest loser pays, member ka due intact)
        # + one item bill (cash) + one due collect
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"], "players": [
                {"label": "Vivek", "type": "member", "memberId": m["id"]},
                {"label": "Guest", "type": "guest"}]})
        s = r.json()
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                       {"winners": [s["players"][0]["pid"]], "cashPaid": 10,
                        "mode": "cash"})
        rb = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"itemId": item["id"], "qty": 1}], "mode": "cash",
            "memberId": m["id"]})
        check(rb.status_code == 201, f"item bill: {rb.text}")
        rp = await api.post(f"/clubs/{cid}/members/{m['id']}/payments",
                            {"amount": 100, "mode": "upi"})
        check(rp.status_code == 200, f"due collect: {rp.text}")
        # ---- stats ----
        st = (await api.get(f"/clubs/{cid}/stats")).json()
        check(st["clubId"] == cid and st["dueLimit"] == 1000
              and st["activeMembers"] == 1 and st["activeSessions"] == 0
              and st["currency"] == "INR" and st["currencySymbol"] == "₹"
              and st["today"] == today_ist()
              and st["runningSessions"] == 0, f"stats keys: {st}")
        # ---- monthly: canonical raw source codes ----
        mon = (await api.get(f"/clubs/{cid}/reports/monthly")).json()
        raw = {row["source"] for row in mon["rows"]}
        check(raw <= {"frames", "items", "due"}, f"canonical raw codes: {raw}")
        check(mon["counts"]["itemBills"] >= 1 and mon["counts"]["duePayments"] >= 1,
              "monthly counts aliases")
        check(all("dueCollections" in d and "balance" in d for d in mon["daily"]),
              "daily aliases (canonical)")
        # ---- monthly AS WEB: Title labels ----
        monw = (await api.get(f"/clubs/{cid}/reports/monthly?client=web")).json()
        labels = {row["source"] for row in monw["rows"]}
        check(labels <= {"Frame", "Item Bill", "Membership", "Due Collection",
                         "Tournament"} and "Frame" in labels,
              f"web Title labels: {labels}")
        check(monw["sourceTotals"] == mon["sourceTotals"],
              "web/canonical totals identical")
        # ---- finance aliases ----
        fin = (await api.get(f"/clubs/{cid}/reports/finance?client=web")).json()
        inc = fin["income"]
        check(inc["dueCollections"] == inc["due"] == 100.0,
              f"finance dueCollections: {inc.get('dueCollections')}")
        check(inc["counts"]["itemBills"] == inc["counts"]["items"] == 1
              and inc["counts"]["duePayments"] == inc["counts"]["due"] == 1,
              f"finance counts aliases: {inc['counts']}")
        fin2 = (await api.get(f"/clubs/{cid}/reports/finance")).json()
        check(fin2["income"]["counts"]["itemBills"] == 1,
              "counts aliases present for app too")
        await master.close()
    go(main())


def test_member_web_fields_planpaymentmode(api):
    """Web MemberModal: passFramesLeft seed on create + planPaymentMode (mode
    nahi) — sale ledger me SAHI mode. updatedAt har mutation pe stamp."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        plan = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Pass 10", "type": "pass", "amount": 500, "frames": 10,
            "days": 30})).json()
        r = await api.post(f"/clubs/{cid}/members", {
            "name": "Aman", "passFramesLeft": 3, "planId": plan["id"],
            "planPaymentMode": "upi"})
        check(r.status_code == 201, f"create: {r.text}")
        mm = r.json()
        check(mm["passFramesLeft"] == 13, f"3 seeded + 10 plan: {mm['passFramesLeft']}")
        check(mm.get("updatedAt"), "updatedAt on create")
        check(mm["planName"] == "Pass 10", "plan applied")
        data = (await api.get(f"/clubs/{cid}/data")).json()
        check(data["sales"][0]["mode"] == "upi"
              and data["sales"][0]["origin"] == "join",
              f"sale booked in planPaymentMode: {data['sales'][0].get('mode')}")
        # frames/bills in club data carry web aliases
        check("membershipSales" in data and "serverNow" in data, "data feed keys")
        # PATCH with planPaymentMode alias (different plan resell)
        plan2 = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Wallet 200", "type": "wallet", "amount": 200,
            "value": 220})).json()
        r = await api.patch(f"/clubs/{cid}/members/{mm['id']}",
                            {"planId": plan2["id"], "planPaymentMode": "card"})
        check(r.status_code == 200, f"patch sell: {r.text}")
        mm = r.json()
        check(mm["walletBalance"] == 220 and mm.get("updatedAt"), "wallet plan via patch")
        data = (await api.get(f"/clubs/{cid}/data")).json()
        check(data["sales"][0]["mode"] == "card", "patch sale mode=card")
        # same-plan echo → Nothing to update, no double charge
        r = await api.patch(f"/clubs/{cid}/members/{mm['id']}",
                            {"planId": plan2["id"]})
        check(r.status_code == 400 and "Nothing to update" in r.json()["detail"],
              "same plan echo no-op")
        check(len(data["sales"]) == 2, "exactly 2 sales")
        await master.close()
    go(main())
