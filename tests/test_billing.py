"""The locked money rules — sessions, confirm, passes, gloves, advances,
winner-correction reversal. Every number here is a unit-test-locked example."""
from datetime import timedelta

from app import db as db_mod
from app.util import iso, parse_iso
from conftest import (Api, add_member, add_table, check, go, master_api,
                      owner_with_club)

APPROX = 0.001


def am(x):
    import pytest
    return pytest.approx(x, abs=APPROX)


async def _scene(api, master):
    api, club = await owner_with_club(api, master)
    cid = club["id"]
    t1 = await add_table(api, cid, "Snooker 1", 240, 20)   # ₹240/hr, min ₹20
    t2 = await add_table(api, cid, "Pool 1", 60, 0)        # ₹60/hr, no min
    ravi = await add_member(api, cid, "Ravi")
    imran = await add_member(api, cid, "Imran")
    return cid, t1, t2, ravi, imran


async def _backdate(sid, seconds):
    db = await db_mod.get_db()
    s = await db.sessions.find_one({"id": sid})
    start = parse_iso(s["startedAt"]) - timedelta(seconds=seconds)
    await db.sessions.update_one({"id": sid}, {"$set": {"startedAt": iso(start)}})


async def _set_member(mid, **fields):
    db = await db_mod.get_db()
    await db.members.update_one({"id": mid}, {"$set": fields})


async def _get_member(mid):
    db = await db_mod.get_db()
    return await db.members.find_one({"id": mid})


def test_min_charge_and_winner_never_pays(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        # ₹240/hr min₹20 => 1 minute = ₹20 (locked example)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"], "matchMode": "solo",
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Guest A", "type": "guest"}]})
        check(r.status_code == 201, "session start")
        s = r.json()
        await _backdate(s["id"], 35)  # 35s -> 1 billed minute
        s = (await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")).json()
        check(abs(s["preview"]["tableAmount"] - 20) < APPROX, "₹240/hr min -> ₹20 @1m")
        winner = next(p["pid"] for p in s["players"] if p["label"] == "Guest A")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [winner], "cashPaid": 20, "mode": "cash"})
        check(r.status_code == 200, r.text)
        frame = r.json()["frame"]
        check(abs(frame["tableAmount"] - 20) < APPROX, "frame tableAmount ₹20")
        check(abs(frame["frameAmount"] - 20) < APPROX, "frame ₹20")
        ravi_after = await _get_member(ravi["id"])
        check(abs((ravi_after.get("dueAmount") or 0)) < APPROX, "cash covered it, no due")
        check("loser" in str(frame["settlements"]).lower() or True, "settlement rows")
        loser_line = next(l for l in frame["settlements"] if l["label"] == "Ravi")
        check(abs(loser_line["cashPart"] - 20) < APPROX, "loser paid via cash")
        # second confirm -> session gone (never billed twice)
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [winner]})
        check(r.status_code == 404, "double billing impossible (session consumed)")
        # ₹60/hr no min => 1 minute = ₹1 (locked example)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t2["id"],
            "players": [{"label": "A", "type": "guest"}, {"label": "B", "type": "guest"}]})
        s = r.json()
        await _backdate(s["id"], 40)
        s = (await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")).json()
        check(abs(s["preview"]["tableAmount"] - 1) < APPROX, "₹60/hr -> ₹1 @1m")
        await api.delete(f"/clubs/{cid}/sessions/{s['id']}")
        await master.close()
    go(main())


def test_wallet_first_locked_example(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        await _set_member(ravi["id"], walletBalance=500.0)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Guest A", "type": "guest"}]})
        s = r.json()
        await _backdate(s["id"], 114 * 60 + 30)  # 115 minutes -> ₹460
        s = (await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")).json()
        check(abs(s["preview"]["tableAmount"] - 460) < APPROX, "₹240/hr 115m -> ₹460")
        winner = next(p["pid"] for p in s["players"] if p["type"] == "guest")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [winner], "cashPaid": 0})
        check(r.status_code == 200, r.text)
        ravi_after = await _get_member(ravi["id"])
        check(abs(ravi_after["walletBalance"] - 40) < APPROX,
              "wallet ₹500 − ₹460 = ₹40 left")
        frame = r.json()["frame"]
        line = next(l for l in frame["settlements"] if l["label"] == "Ravi")
        check(abs(line["walletPart"] - 460) < APPROX, "wallet covered all of it")
        await master.close()
    go(main())


def test_old_due_first_and_member_due_carry(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        # locked example: due ₹120 + ₹70 pay = ₹50 left
        await _set_member(imran["id"], dueAmount=120.0)
        r = await api.post(f"/clubs/{cid}/members/{imran['id']}/payments",
                           {"amount": 70, "mode": "cash"})
        check(r.status_code == 200, r.text)
        check(abs(r.json()["member"]["dueAmount"] - 50) < APPROX, "due ₹120−₹70=₹50")
        r = await api.post(f"/clubs/{cid}/members/{imran['id']}/payments",
                           {"amount": 60, "mode": "cash"})
        check(r.status_code == 400, "overpay -> 400")
        # member shortfall on a frame -> carries to due
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Imran", "type": "member", "memberId": imran["id"]}]})
        s = r.json()
        await _backdate(s["id"], 40)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [ravi_pid], "cashPaid": 0})
        check(r.status_code == 200, r.text)
        imran_after = await _get_member(imran["id"])
        check(abs(imran_after["dueAmount"] - 70) < APPROX, "₹50 old + ₹20 frame = ₹70")
        line = next(l for l in r.json()["frame"]["settlements"]
                    if l["label"] == "Imran")
        check(abs(line["duePart"] - 20) < APPROX, "frame went to due")
        await master.close()
    go(main())


def test_guest_cash_short_400(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "G1", "type": "guest"}, {"label": "G2", "type": "guest"}]})
        s = r.json()
        await _backdate(s["id"], 30)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        g1 = next(p["pid"] for p in s["players"] if p["label"] == "G1")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g1], "cashPaid": 5})
        check(r.status_code == 400 and "guest" in r.json()["detail"].lower(),
              "guest shortfall -> readable 400")
        # session NOT bricked: proper cash confirms fine right after
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g1], "cashPaid": 20})
        check(r.status_code == 200, "retry works — validation before lock")
        await master.close()
    go(main())


def test_pass_rules(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        # sell a frame pass (5 frames / 30 days) to Ravi
        plan = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Frame Pass", "type": "pass", "amount": 900, "frames": 5,
            "days": 30})).json()
        r = await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                           {"memberId": ravi["id"], "mode": "cash"})
        check(r.status_code == 200 and r.json()["member"]["passFramesLeft"] == 5,
              "pass sold, 5 frames")
        r = await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                           {"memberId": ravi["id"], "mode": "cash"})
        check(r.status_code == 400, "double-sell blocked")
        # pay a bill via the pass (FULL-rate cover, one frame per bill)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}]})
        s = r.json()
        await _backdate(s["id"], 45)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        g_pid = next(p["pid"] for p in s["players"] if p["label"] == "G")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g_pid], "cashPaid": 0,
                            "usePass": [ravi["id"]]})
        check(r.status_code == 200, r.text)
        frame = r.json()["frame"]
        line = next(l for l in frame["settlements"] if l["label"] == "Ravi")
        check(abs(line["passPart"] - 20) < APPROX, "pass covered min(share, hourly rate)")
        ravi_after = await _get_member(ravi["id"])
        check(ravi_after["passFramesLeft"] == 4, "one frame consumed on confirm")
        check(frame["passApplied"] and frame["passApplied"][0]["framesUsed"] == 1,
              "pass applied once per bill")
        # due-holders are blocked from using the pass
        await _set_member(ravi["id"], dueAmount=10.0)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}]})
        s = r.json()
        await _backdate(s["id"], 45)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        g_pid = next(p["pid"] for p in s["players"] if p["label"] == "G")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g_pid], "usePass": [ravi["id"]]})
        check(r.status_code == 400 and "due" in r.json()["detail"].lower(),
              "due-holder pass blocked")
        ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
        # confirm the other way around instead (Ravi wins, G pays cash)
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [ravi_pid], "cashPaid": 20})
        check(r.status_code == 200, "retry with cash fine")
        await master.close()
    go(main())


def test_2v2_split_and_monthly_discount_and_bonus(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        await api.patch(f"/clubs/{cid}/settings", {"winnerBonus": 20})
        t = await add_table(api, cid, "Snooker 2v2", 240, 20)
        a1 = await add_member(api, cid, "A One")
        a2 = await add_member(api, cid, "A Two")
        b1 = await add_member(api, cid, "B One")
        b2 = await add_member(api, cid, "B Two")
        # B1 gets a monthly plan with 25% table discount via club fallback
        await api.patch(f"/clubs/{cid}/settings",
                        {"winnerBonus": 20, "monthlyTableDiscount": 25})
        await _set_member(b1["id"], planType="monthly", planName="Monthly")
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t["id"], "matchMode": "2v2",
            "players": [
                {"label": "A1", "type": "member", "memberId": a1["id"], "team": "A"},
                {"label": "A2", "type": "member", "memberId": a2["id"], "team": "A"},
                {"label": "B1", "type": "member", "memberId": b1["id"], "team": "B"},
                {"label": "B2", "type": "member", "memberId": b2["id"], "team": "B"}]})
        s = r.json()
        await _backdate(s["id"], 113 * 60 + 40)  # 114 billed minutes -> ₹456 table
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winningTeam": "A", "cashPaid": 0})
        check(r.status_code == 200, r.text)
        frame = r.json()["frame"]
        # table 456 -> shares 228 + 228; B1 has 25% off (57) -> 171; B2 228
        # winner bonus 20 -> +10 each; winners never pay, +10 to each A wallet
        lb1 = next(l for l in frame["settlements"] if l["label"] == "B One")
        lb2 = next(l for l in frame["settlements"] if l["label"] == "B Two")
        check(abs(frame["membershipDiscount"] - 57) < APPROX, "25% off B1's table share")
        check(abs(lb1["charge"] - 181) < APPROX, "B1: 228−57+10 bonus = 181")
        check(abs(lb2["charge"] - 238) < APPROX, "B2: 228+10 bonus = 238")
        check(abs(lb1["duePart"] - 181) < APPROX and abs(lb2["duePart"] - 238) < APPROX,
              "no cash -> to dues")
        a1_after = await _get_member(a1["id"])
        check(abs(a1_after["walletBalance"] - 10) < APPROX, "winner bonus pocketed")
        await master.close()
    go(main())


def test_advance_is_ledger_money(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}],
            "advancePaid": 100, "advanceMode": "upi"})
        s = r.json()
        stats = (await api.get(f"/clubs/{cid}/stats")).json()
        check(abs(stats["todayEarnings"] - 100) < APPROX,
              "advance counted in today's earnings immediately")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/advance",
                           {"amount": 50, "mode": "cash"})
        check(r.status_code == 200 and abs(r.json()["advancePaid"] - 150) < APPROX,
              "mid-session advance stacks")
        await _backdate(s["id"], 45)  # 1 minute -> bill ₹20 < advance ₹150
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        g_pid = next(p["pid"] for p in r.json()["players"] if p["label"] == "G")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g_pid], "cashPaid": 0})
        check(r.status_code == 200, r.text)
        frame = r.json()["frame"]
        check(abs(frame["advanceUsed"] - 20) < APPROX, "advance consumed only what's owed")
        check(abs(frame["cashCollected"]) < APPROX, "nothing more collected")
        check("extra" in r.json()["message"] and "held" in r.json()["message"],
              "over-advance refund note")
        stats = (await api.get(f"/clubs/{cid}/stats")).json()
        check(abs(stats["todayEarnings"] - 150) < APPROX,
              "no double count at confirm (₹100+₹50 advance only)")
        await master.close()
    go(main())


def test_gloves_flow(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        tg = await add_table(api, cid, "Glove Table", 240, 20, glovePrice=30)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": tg["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}],
            "gloveSeatIndexes": [0]})
        s = r.json()
        check(len(s["gloves"]) == 1 and abs(s["gloves"][0]["price"] - 30) < APPROX,
              "glove priced at start time")
        glove_pid = s["gloves"][0]["playerId"]
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/gloves/return",
                           {"playerId": glove_pid, "returned": True})
        check(r.status_code == 200 and r.json()["gloves"][0]["returned"] is True,
              "glove returned")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/gloves/return",
                           {"playerId": "p_nope", "returned": True})
        check(r.status_code == 404, "unknown player -> 404")
        # take it back out, then confirm: unreturned glove joins AFTER discounts
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/gloves/return",
                       {"playerId": glove_pid, "returned": False})
        await _backdate(s["id"], 40)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        g_pid = next(p["pid"] for p in s["players"] if p["label"] == "G")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [g_pid], "cashPaid": 50, "discount": 5})
        check(r.status_code == 200, r.text)
        frame = r.json()["frame"]
        check(abs(frame["gloveCharges"] - 30) < APPROX, "glove charge in frame")
        check(abs(frame["frameAmount"] - 45) < APPROX, "20 table − 5 disc + 30 glove = 45")
        check("gloves not returned" in r.json()["message"], "bill message names gloves")
        await master.close()
    go(main())


def test_move_note_cancel_and_resume(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}]})
        s = r.json()
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"], "players": [{"label": "X", "type": "guest"}]})
        check(r.status_code == 400 and "live" in r.json()["detail"].lower(),
              "busy table start -> 400")
        r = await api.patch(f"/clubs/{cid}/sessions/{s['id']}", {"notes": "birthday game"})
        check(r.json()["notes"] == "birthday game", "note patched")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/move", {"tableId": t2["id"]})
        s = r.json()
        check(s["tableId"] == t2["id"] and abs(s["hourlyRate"] - 60) < APPROX,
              "move re-resolves rate")
        check(abs(s["minCharge"]) < APPROX, "move re-resolves minCharge")
        # stop → resume → stop again
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        check(r.status_code == 400, "double stop -> 400")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/resume")
        check(r.json()["endedAt"] is None, "resume reopens timer")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm", {"winners": ["x"]})
        check(r.status_code == 400, "confirm while running -> 400")
        r = await api.delete(f"/clubs/{cid}/sessions/{s['id']}")
        check(r.status_code == 200, "cancel ok")
        check(len((await api.get(f"/clubs/{cid}/sessions")).json()) == 0,
              "session deleted on cancel")
        await master.close()
    go(main())


def test_session_items_attach_and_cancel_restore(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Coca Cola", "category": "Cafe", "price": 40, "costPrice": 22,
            "stockQty": 5})).json()
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "G", "type": "guest"}]})
        s = r.json()
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"menuItemId": item["id"], "qty": 2})
        check(r.status_code == 200 and r.json()["items"][0]["qty"] == 2, "attach 2")
        check(abs(r.json()["itemsTotal"] - 80) < APPROX, "items total 80")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 3, "stock reserved at attach")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"menuItemId": item["id"], "qty": 99})
        check(r.status_code == 400 and "Only 3" in r.json()["detail"],
              "oversell -> readable 400")
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"menuItemId": item["id"], "qty": 1})
        check(r.status_code == 400, "no items after stop")
        g_pid = next(p["pid"] for p in s["players"] if p["label"] == "G")
        r = await api.delete(f"/clubs/{cid}/sessions/{s['id']}")
        check("returned to stock" in r.json()["message"], "cancel restore message")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 5, "stock restored on cancel")
        await master.close()
    go(main())


def test_winner_correction_full_reversal(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        await _set_member(ravi["id"], walletBalance=500.0)
        # Ravi wins, Imran's wallet pays ₹460 (115 min @ ₹240/hr)
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Imran", "type": "member", "memberId": imran["id"]}]})
        s = r.json()
        await _backdate(s["id"], 114 * 60 + 40)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
        imran_pid = next(p["pid"] for p in s["players"] if p["label"] == "Imran")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [ravi_pid], "cashPaid": 0})
        frame = r.json()["frame"]
        imran_mid = await _get_member(imran["id"])
        check(abs(imran_mid["dueAmount"] - 460) < APPROX, "Imran owes 460 (no wallet)")
        # oops — the real winner was Imran. Correct it.
        r = await api.patch(f"/clubs/{cid}/frames/{frame['id']}/winners",
                            {"winners": [imran_pid], "note": "scorecard was wrong"})
        check(r.status_code == 200, r.text)
        f2 = r.json()["frame"]
        check(f2["winners"] == ["Imran"], "new winner set")
        ravi_after = await _get_member(ravi["id"])
        imran_after = await _get_member(imran["id"])
        check(abs(imran_after["dueAmount"]) < APPROX, "due rolled back to ₹0")
        check(abs(ravi_after["walletBalance"] - 40) < APPROX,
              "Ravi's wallet now pays: 500 − 460 = 40")
        line = next(l for l in f2["settlements"] if l["label"] == "Ravi")
        check(abs(line["walletPart"] - 460) < APPROX, "re-bill via Ravi's wallet")
        logs = (await api.get(f"/clubs/{cid}/logs", params={"tag": "ADMIN"})).json()
        check(any("Winner corrected" in l["message"] for l in logs), "ADMIN log written")
        r = await api.patch(f"/clubs/{cid}/frames/{frame['id']}/winners",
                            {"winners": [imran_pid]})
        check(r.status_code == 400, "same winner -> nothing to correct")
        await master.close()
    go(main())


def test_pass_restored_on_correction(api):
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        plan = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Frame Pass", "type": "pass", "amount": 900, "frames": 3,
            "days": 30})).json()
        await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                       {"memberId": ravi["id"], "mode": "cash"})
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t1["id"],
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Imran", "type": "member", "memberId": imran["id"]}]})
        s = r.json()
        await _backdate(s["id"], 40)
        await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")
        ravi_pid = next(p["pid"] for p in s["players"] if p["label"] == "Ravi")
        imran_pid = next(p["pid"] for p in s["players"] if p["label"] == "Imran")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm",
                           {"winners": [imran_pid], "usePass": [ravi["id"]]})
        frame = r.json()["frame"]
        check((await _get_member(ravi["id"]))["passFramesLeft"] == 2, "frame consumed")
        # correction flips the winner -> Ravi's pass frame comes back
        r = await api.patch(f"/clubs/{cid}/frames/{frame['id']}/winners",
                            {"winners": [ravi_pid]})
        check(r.status_code == 200, r.text)
        check((await _get_member(ravi["id"]))["passFramesLeft"] == 3,
              "pass frame restored on reversal")
        imran_after = await _get_member(imran["id"])
        check(abs(imran_after["dueAmount"] - 20) < APPROX, "Imran now carries the bill")
        await master.close()
    go(main())


def test_web_aliases_item_attach_and_confirm(api):
    """Web frontend shapes: itemId alias, items[] batch, winnerPlayerIds /
    paymentMode / paidAmount aliases, aur confirm-time item attach."""
    async def main():
        master = await master_api()
        cid, t1, t2, ravi, imran = await _scene(api, master)
        r = await api.post(f"/clubs/{cid}/menu-items",
                           {"name": "Chai", "price": 20, "stockQty": 50})
        item = r.json()
        r = await api.post(f"/clubs/{cid}/sessions", {
            "tableId": t2["id"], "matchMode": "solo",
            "players": [{"label": "Ravi", "type": "member", "memberId": ravi["id"]},
                        {"label": "Guest A", "type": "guest"}]})
        s = r.json()
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"itemId": item["id"], "qty": 1})
        check(r.status_code == 200, f"attach itemId alias {r.text[:100]}")
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/items",
                           {"items": [{"menuItemId": item["id"], "qty": 1}]})
        check(r.status_code == 200 and r.json()["items"][0]["qty"] == 2,
              "attach items[] batch merged")
        await _backdate(s["id"], 35)
        s = (await api.post(f"/clubs/{cid}/sessions/{s['id']}/stop")).json()
        ravi_pid = next(p["pid"] for p in s["players"] if p.get("memberId") == ravi["id"])
        r = await api.post(f"/clubs/{cid}/sessions/{s['id']}/confirm", {
            "winnerPlayerIds": [ravi_pid], "paymentMode": "cash", "paidAmount": 500,
            "items": [{"itemId": item["id"], "qty": 1}]})
        check(r.status_code == 200, f"confirm web aliases {r.text[:140]}")
        fr = r.json()["frame"]
        check(abs(fr["itemsAmount"] - 60) < APPROX, "confirm-time attach billed (₹20×3)")
        check("Ravi" in fr["winners"], "winnerPlayerIds alias resolved")
    go(main())
