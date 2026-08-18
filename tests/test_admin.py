"""Staff lockdown (403 admin-area walls), team overview, master panel,
platform support config, mail system."""
from conftest import (Api, add_member, add_table, check, dev_login, go,
                      master_api, owner_with_club)


def test_staff_lockdown(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]

        staff = Api()
        staff_user = await dev_login(staff, "staff2@rowdys.dev", "Abhay Koli")
        r = await master.patch(f"/master/users/{staff_user['id']}",
                               {"role": "staff", "clubIds": [cid]})
        check(r.status_code == 200 and r.json()["role"] == "staff", "master assigns staff")

        # money-admin surfaces -> 403 "Admin area — owner access required"
        for url in (f"/clubs/{cid}/reports/monthly", f"/clubs/{cid}/reports/finance",
                    f"/clubs/{cid}/reports/day-close", f"/clubs/{cid}/reports/utilisation",
                    f"/clubs/{cid}/expenses", "/team"):
            r = await staff.get(url)
            check(r.status_code == 403 and "Admin area" in r.json()["detail"],
                  f"staff 403 on GET {url}")
        r = await staff.post(f"/clubs/{cid}/expenses", {
            "title": "X", "category": "misc", "amount": 10, "date": "2026-08-10"})
        check(r.status_code == 403, "staff 403 on expense write")
        r = await api.post(f"/clubs/{cid}/expenses", {
            "title": "X", "category": "misc", "amount": 10, "date": "2026-08-10"})
        check(r.status_code == 201, "owner expense ok")

        # everything else stays open for staff
        check((await staff.get(f"/clubs/{cid}/data")).status_code == 200, "staff reads data")
        check((await staff.get(f"/clubs/{cid}/members")).status_code == 200, "staff reads members")
        r = await staff.post(f"/clubs/{cid}/members", {"name": "Walk In Star"})
        check(r.status_code == 201, "staff can add players")
        r = await staff.get("/clubs")
        check(r.status_code == 200 and any(c["id"] == cid for c in r.json()),
              "staff sees assigned club")

        # 403 fires BEFORE 402: expire the owner's sub, staff keeps getting 403
        users = (await master.get("/master/users", params={"q": "owner"})).json()
        owner_id = next(u["id"] for u in users if u["email"] == "owner@rowdys.dev")
        await master.patch(f"/master/users/{owner_id}/subscription", {"status": "expired"})
        r = await staff.get(f"/clubs/{cid}/reports/monthly")
        check(r.status_code == 403, "staff 403 beats owner-402")
        r = await api.post(f"/clubs/{cid}/tables", {"name": "T", "rate": {"hourlyRate": 100}})
        check(r.status_code == 402, "owner 402 when expired")
        r = await staff.post(f"/clubs/{cid}/members", {"name": "Blocked"})
        check(r.status_code == 402, "staff also blocked by owner subscription")
        check((await staff.get(f"/clubs/{cid}/data")).status_code == 200,
              "reads stay open even when locked")
        await master.close(); await staff.close()
    go(main())


def test_team_overview(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        staff = Api()
        staff_user = await dev_login(staff, "staff2@rowdys.dev", "Abhay Koli")
        await master.patch(f"/master/users/{staff_user['id']}",
                           {"role": "staff", "clubIds": [cid]})
        r = await api.get("/team")
        check(r.status_code == 200, "owner team 200")
        row = next(c for c in r.json()["clubs"] if c["club"]["id"] == cid)
        handlers = row["handlers"]
        check(handlers[0]["isOwner"] is True and handlers[0]["email"] == "owner@rowdys.dev",
              "owner first")
        check(any(h["email"] == "staff2@rowdys.dev" and h["role"] == "staff"
                  for h in handlers), "staff row listed")
        check(all("master" not in h["email"] for h in handlers), "masters never listed")
        r = await master.get("/team")
        check(r.status_code == 200, "master team 200")
        await master.close(); await staff.close()
    go(main())


def test_master_panel(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        r = await api.get("/master/overview")
        check(r.status_code == 403, "owner can't see master area")
        ov = (await master.get("/master/overview")).json()
        check(ov["accounts"] >= 1 and ov["totalClubs"] >= 1, "overview counts")
        check("mrr" in ov and ov["sellerPlans"] >= 1, "mrr + plans count")
        users = (await master.get("/master/users", params={"q": "owner@rowdys"})).json()
        check(len(users) == 1, "search by email")
        users = (await master.get("/master/users", params={"q": cid})).json()
        check(any(u["email"] == "owner@rowdys.dev" for u in users), "search by club id")
        owner = next(u for u in users if u["email"] == "owner@rowdys.dev")
        r = await master.patch(f"/master/users/{owner['id']}/subscription",
                               {"status": "active", "notes": "paid via UPI"})
        check(r.json()["subscription"]["status"] == "active", "sub activate")
        # plan catalog CRUD + safety (owner still holds the Pro plan here)
        plan = (await master.get("/subscription-plans")).json()[0]
        r = await master.delete(f"/master/subscription-plans/{plan['id']}")
        check(r.status_code == 400, "plan in use can't be deleted")
        r = await master.post(f"/master/subscription-plans/{plan['id']}/toggle-active")
        check(r.json()["active"] is False, "toggle off")
        r = await master.post("/master/subscription-plans", {
            "name": "Starter", "price": 499, "durationDays": 30, "maxClubs": 1})
        p2 = r.json()
        r = await master.delete(f"/master/subscription-plans/{p2['id']}")
        check(r.status_code == 200, "unused plan deleted")
        r = await master.delete(f"/master/users/{owner['id']}/subscription")
        check(r.json()["subscription"] is None, "sub deleted")
        mails = (await master.get("/master/mailouts")).json()
        kinds = {m["kind"] for m in mails}
        check("subscription" in kinds, "subscription mails recorded")
        check(all("html" not in m for m in mails), "html stripped from listing")
        await master.close()
    go(main())


def test_platform_support(api):
    async def main():
        master = await master_api()
        await dev_login(api, "owner@rowdys.dev", "Raju")
        s = (await api.get("/platform/support")).json()
        check(s["email"] == "master@rowdys.dev", "fallback = first master email")
        r = await api.patch("/platform/support", {"email": "help@x.com"})
        check(r.status_code == 403, "non-master can't patch")
        r = await master.patch("/platform/support", {"phone": "12345"})
        check(r.status_code == 400, "bad phone -> 400 readable")
        r = await master.patch("/platform/support", {"email": "nope"})
        check(r.status_code == 400, "bad email -> 400")
        r = await master.patch("/platform/support",
                               {"email": "care@rowdys.dev",
                                "phone": "+91 98290-12345"})
        check(r.status_code == 200 and r.json()["phone"] == "+919829012345",
              "phone cleaned")
        s = (await api.get("/platform/support")).json()
        check(s["email"] == "care@rowdys.dev", "owner reads saved contact")
        await master.close()
    go(main())


def test_notify_and_balance_mail(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        no_mail = await add_member(api, cid, "NoEmail")
        r = await api.post(f"/clubs/{cid}/members/{no_mail['id']}/notify",
                           {"channel": "email"})
        check(r.status_code == 400 and "email" in r.json()["detail"].lower(),
              "notify without email -> 400")
        with_mail = await add_member(api, cid, "Mailed", "9811111111", "ravi@x.com")
        r = await api.post(f"/clubs/{cid}/members/{with_mail['id']}/notify",
                           {"channel": "email"})
        check(r.status_code == 200, "notify ok")
        mails = (await master.get("/master/mailouts")).json()
        check(any(m["kind"] == "balance_notify" and m["to"] == "ravi@x.com"
                  and m["sent"] is False for m in mails),
              "mail recorded (record-only without SMTP)")
        await master.close()
    go(main())


def test_subscription_welcome_and_expiry_sweep(api):
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        # plan sale mail fires for members with an email
        member = await add_member(api, cid, "Ravi Mail", "9811111111", "ravi@x.com")
        plan = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Monthly", "type": "monthly", "amount": 1500, "days": 30,
            "tableDiscountPercent": 10})).json()
        await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                       {"memberId": member["id"], "mode": "cash"})
        mails = (await master.get("/master/mailouts")).json()
        check(any(m["kind"] == "plan_sale" for m in mails), "plan sale mail recorded")
        # expiry sweep on /data load — set expiry in the past, load, mail + warning
        past = await api.patch(f"/clubs/{cid}/members/{member['id']}",
                               {"planExpiresAt": "2026-01-01"})
        check(past.status_code == 200, "admin expiry override ok")
        r = await api.patch(f"/clubs/{cid}/members/{member['id']}",
                            {"planExpiresAt": "not-a-date"})
        check(r.status_code == 400, "bad expiry date -> 400")
        before = len((await master.get("/master/mailouts")).json())
        await api.get(f"/clubs/{cid}/data")
        await api.get(f"/clubs/{cid}/data")  # idempotent — sweep runs once
        after = (await master.get("/master/mailouts")).json()
        expired = [m for m in after if m["kind"] == "plan_expired"]
        check(len(expired) == 1, "expiry mail recorded exactly once (idempotent)")
        logs = (await api.get(f"/clubs/{cid}/logs", params={"tag": "WARNING"})).json()
        check(any("Membership expired" in l["message"] for l in logs), "WARNING logged")
        await master.close()
    go(main())


def test_member_opening_balances(api):
    """Member create accepts opening wallet/due (migrated from old register)."""
    async def main():
        master = await master_api()
        _, club = await owner_with_club(api, master)
        cid = club["id"]
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Ledger Wale", "phone": "9700000000",
                            "walletBalance": 500, "dueAmount": 125.5})
        check(r.status_code == 201, "create with opening balances")
        m = r.json()
        check(m.get("walletBalance") == 500.0, "wallet stored")
        check(m.get("dueAmount") == 125.5, "due stored (2dp exact)")
        r = await api.post(f"/clubs/{cid}/members",
                           {"name": "Neg", "walletBalance": -5})
        check(r.status_code == 400, "negative opening wallet rejected")  # app maps 422 → 400
        listed = (await api.get(f"/clubs/{cid}/members")).json()
        got = next(x for x in listed if x["id"] == m["id"])
        check(got["walletBalance"] == 500.0 and got["dueAmount"] == 125.5,
              "balances visible in list")
        await master.close()
    go(main())
