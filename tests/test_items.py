"""Menu items, restock (weighted cost + auto expense), counter item bills,
mark-paid, delete-as-reversal, mixed payments."""
from conftest import (Api, add_member, check, go, master_api, owner_with_club)


async def _scene(api, master):
    api, club = await owner_with_club(api, master)
    return club["id"]


def test_menu_items_crud_and_restock(api):
    async def main():
        master = await master_api()
        cid = await _scene(api, master)
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Coca Cola", "category": "Cafe", "price": 40, "costPrice": 0,
            "stockQty": 0, "reorderLevel": 3})).json()
        check(item["reorderLevel"] == 3, "reorder level stored")
        r = await api.post(f"/clubs/{cid}/menu-items/{item['id']}/restock",
                           {"qty": 10, "unitCost": 22})
        check(r.json()["item"]["stockQty"] == 10, "stock += qty")
        check(abs(r.json()["item"]["costPrice"] - 22) < 0.001, "first cost set")
        r = await api.post(f"/clubs/{cid}/menu-items/{item['id']}/restock",
                           {"qty": 10, "unitCost": 42})
        check(abs(r.json()["item"]["costPrice"] - 32) < 0.001,
              "weighted average cost (10@22 + 10@42) = 32")
        expenses = (await api.get(f"/clubs/{cid}/expenses")).json()
        stock_exp = [e for e in expenses["rows"] if e["category"] == "stock"]
        check(len(stock_exp) == 2 and abs(stock_exp[0]["amount"] - 420) < 0.001,
              "restock auto-expenses land in stock category")
        check("Stock purchase · Coca Cola ×10" in stock_exp[0]["title"],
              "auto expense title")
        r = await api.patch(f"/clubs/{cid}/menu-items/{item['id']}", {"price": 45})
        check(r.json()["price"] == 45, "price patch")
        r = await api.delete(f"/clubs/{cid}/menu-items/{item['id']}")
        check(r.status_code == 200, "delete item")
        r = await api.get(f"/clubs/{cid}/menu-items")
        check(all(i["id"] != item["id"] for i in r.json()), "item gone")
        await master.close()
    go(main())


def test_item_bill_modes_and_lifecycle(api):
    async def main():
        master = await master_api()
        cid = await _scene(api, master)
        honey = await add_member(api, cid, "Honey", "9812345678")
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Samosa", "category": "Cafe", "price": 25, "costPrice": 12,
            "stockQty": 10})).json()

        # cash bill — member picked, name blank -> falls back to member name
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 2}],
            "memberId": honey["id"], "mode": "cash"})
        check(r.status_code == 201, r.text)
        bill = r.json()["bill"]
        check(bill["customerName"] == "Honey", "member name fallback")
        check(abs(bill["amount"] - 50) < 0.001 and abs(bill["profit"] - 26) < 0.001,
              "amount + est profit")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 8, "stock decremented")

        # oversell -> readable 400, stock unchanged
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 99}],
            "customerName": "Walk in", "mode": "cash"})
        check(r.status_code == 400 and "Only 8" in r.json()["detail"], "oversell 400")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 8, "oversell didn't touch stock")

        # no member + no name -> 400
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 1}], "mode": "cash"})
        check(r.status_code == 400 and "Customer name" in r.json()["detail"],
              "name required without member")

        # due-mode bill lands on the member's due
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 2}],
            "memberId": honey["id"], "mode": "due"})
        due_bill = r.json()["bill"]
        check(due_bill["paid"] is False and due_bill["status"] == "unpaid", "unpaid due bill")
        members = (await api.get(f"/clubs/{cid}/members")).json()
        honey_m = next(m for m in members if m["id"] == honey["id"])
        check(abs(honey_m["dueAmount"] - 50) < 0.001, "due += bill")

        # mark-paid settles and credits the received month
        r = await api.post(f"/clubs/{cid}/item-bills/{due_bill['id']}/mark-paid",
                           {"mode": "upi"})
        check(r.json()["bill"]["paid"] is True, "marked paid")
        honey_m = next(m for m in (await api.get(f"/clubs/{cid}/members")).json()
                       if m["id"] == honey["id"])
        check(abs(honey_m["dueAmount"]) < 0.001, "due rolled back on pay")
        logs = (await api.get(f"/clubs/{cid}/logs", params={"tag": "PAYMENT"})).json()
        settle = [l for l in logs if l.get("refId") == due_bill["id"]
                  and l.get("mode") == "upi" and "settled" in l["message"]]
        check(len(settle) == 1, "settled PAYMENT logged")
        await master.close()
    go(main())


def test_wallet_and_mixed_and_delete_reversal(api):
    async def main():
        master = await master_api()
        cid = await _scene(api, master)
        ravi = await add_member(api, cid, "Ravi")
        plan = (await api.post(f"/clubs/{cid}/plans", {
            "name": "Gold Wallet", "type": "wallet", "amount": 400, "value": 500})).json()
        await api.post(f"/clubs/{cid}/plans/{plan['id']}/sell",
                       {"memberId": ravi["id"], "mode": "cash"})
        item = (await api.post(f"/clubs/{cid}/menu-items", {
            "name": "Cold Coffee", "category": "Cafe", "price": 90, "costPrice": 45,
            "stockQty": 6})).json()

        # wallet shortfall -> 400 (before touching the good bill)
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 6}],
            "memberId": ravi["id"], "mode": "wallet"})
        check(r.status_code == 400 and "Wallet balance is short" in r.json()["detail"],
              "wallet short -> readable 400")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 6, "failed wallet bill kept stock intact")

        # wallet bill happy path
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 5}], "discount": 50,
            "memberId": ravi["id"], "mode": "wallet"})
        wallet_bill = r.json()["bill"]
        check(abs(wallet_bill["amount"] - 400) < 0.001, "450 − 50 discount = 400")
        ravi_m = next(m for m in (await api.get(f"/clubs/{cid}/members")).json()
                      if m["id"] == ravi["id"])
        check(abs(ravi_m["walletBalance"] - 100) < 0.001, "wallet 500 − 400 = 100")
        stock = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(stock["stockQty"] == 1, "stock decremented by sale")

        # top the stock back up for the mixed-payment cases
        await api.post(f"/clubs/{cid}/menu-items/{item['id']}/restock",
                       {"qty": 9, "unitCost": 45})

        # mixed payments must add up
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 1}], "customerName": "Mix Man",
            "mode": "mixed", "payments": [{"mode": "cash", "amount": 50},
                                          {"mode": "upi", "amount": 30}]})
        check(r.status_code == 400, "mixed mismatch -> 400")
        r = await api.post(f"/clubs/{cid}/item-bills", {
            "items": [{"menuItemId": item["id"], "qty": 1}], "customerName": "Mix Man",
            "mode": "mixed", "payments": [{"mode": "cash", "amount": 50},
                                          {"mode": "upi", "amount": 40}]})
        check(r.status_code == 201, "mixed ok when sums match")

        # delete = reversal + stock restore (+ wallet refund for wallet bills)
        before = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                      if i["id"] == item["id"])
        r = await api.delete(f"/clubs/{cid}/item-bills/{wallet_bill['id']}")
        check(r.status_code == 200, "delete bill")
        after = next(i for i in (await api.get(f"/clubs/{cid}/menu-items")).json()
                     if i["id"] == item["id"])
        check(after["stockQty"] - before["stockQty"] == 5, "stock restored on delete")
        ravi_m = next(m for m in (await api.get(f"/clubs/{cid}/members")).json()
                      if m["id"] == ravi["id"])
        check(abs(ravi_m["walletBalance"] - 500) < 0.001, "wallet refunded on delete")
        await master.close()
    go(main())
