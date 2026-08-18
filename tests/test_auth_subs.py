"""Auth, subscription plans, 402 walls, clubs & tenant isolation."""
from conftest import (Api, add_member, add_table, check, dev_login, go,
                      make_seller_plan, master_api, owner_with_club)


def test_health_and_auth_walls(api):
    async def main():
        check((await api.get("/health")).json()["ok"] is True, "health ok")
        r = await api.client.get("/")
        check(r.status_code == 200 and "Rowdy" in r.json()["name"], "root")
        check((await api.get("/clubs")).status_code == 401, "no token -> 401")
        api.auth("garbage")
        r = await api.get("/clubs")
        check(r.status_code == 401, "bad token -> 401")
        check("session expired" in r.json()["detail"], "friendly 401 copy")
        r = await api.post("/auth/dev", {"email": "not-an-email"})
        check(r.status_code == 400, "bad email -> 400 readable")
        r = await api.post("/auth/google", {"idToken": "x" * 20})
        check(r.status_code in (400, 401),
              "google -> 400 unconfigured or 401 unverifiable")
    go(main())


def test_profile_and_disabled_user(api):
    async def main():
        user = await dev_login(api, "owner@rowdys.dev", "Raju Bhai")
        r = await api.patch("/auth/me", {"name": "Raju Bhaiya", "phone": "9829012345",
                                         "location": "Jaipur"})
        check(r.status_code == 200 and r.json()["phone"] == "9829012345", "profile patch")
        check((await api.get("/auth/me")).json()["location"] == "Jaipur", "me reflects")
        master = await master_api()
        users = (await master.get("/master/users", params={"q": "owner"})).json()
        uid = next(u["id"] for u in users if u["email"] == "owner@rowdys.dev")
        r = await master.patch(f"/master/users/{uid}", {"active": False})
        check(r.status_code == 200 and r.json()["active"] is False, "master disables")
        r = await api.get("/clubs")
        check(r.status_code == 403 and "disabled" in r.json()["detail"], "disabled -> 403")
        await master.close()
    go(main())


def test_plan_catalog_and_select(api):
    async def main():
        master = await master_api()
        plan = await make_seller_plan(api, master)
        check((await api.get("/subscription-plans")).json()[0]["name"] == "Pro",
              "public catalog")
        r = await master.patch(f"/master/subscription-plans/{plan['id']}",
                               {"price": 1199})
        check(r.json()["price"] == 1199, "master patch plan")
        await dev_login(api, "newbie@rowdys.dev", "New B")
        r = await api.post("/clubs", {"name": "X Club"})
        check(r.status_code == 402, "no subscription -> 402 on club create")
        r = await api.post("/account/subscription/select", {"planId": plan["id"]})
        check(r.status_code == 200, "select plan")
        sub = r.json()["subscription"]
        check(sub["status"] == "trial" and sub["expiresAt"], "trial is instant w/ expiry")
        check((await api.get("/account/subscription")).json()["subscription"]["planName"]
              == "Pro", "subscription snapshot")
        # 402 wall on billing mutations when subscription is expired
        r = await master.patch(
            f"/master/users/{r.json()['user']['id']}/subscription",
            {"status": "expired"})
        check(r.status_code == 200, "master expires sub")
        r = await api.post("/clubs", {"name": "X Club"})
        check(r.status_code == 402, "expired -> still 402")
        await master.close()
    go(main())


def test_clubs_max_and_tenant(api):
    async def main():
        master = await master_api()
        other = Api()
        _, club = await owner_with_club(api, master)   # plan maxClubs=1
        r = await api.post("/clubs", {"name": "Second Den"})
        check(r.status_code == 400 and "club" in r.json()["detail"].lower(),
              "maxClubs readable 400")
        check(len((await api.get("/clubs")).json()) == 1, "one club listed")
        r = await api.patch(f"/clubs/{club['id']}/settings",
                            {"winnerBonus": 50, "dueLimit": 500})
        check(r.json()["settings"]["winnerBonus"] == 50, "settings patch")
        await dev_login(other, "other@rowdys.dev", "Other Bhai")
        r = await other.get(f"/clubs/{club['id']}")
        check(r.status_code == 403, "tenant isolation 403")
        r = await other.get(f"/clubs/{club['id']}/members")
        check(r.status_code == 403, "tenant on members too")
        add = await add_table(api, club["id"], "T1", 100, 0)
        r = await other.get(f"/clubs/{club['id']}/tables")
        check(r.status_code == 403, "tables tenant too")
        await master.close(); await other.close()
    go(main())
