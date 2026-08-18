"""Expenses — OWNER-ONLY admin surface (staff: 403 on read AND write)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db as db_mod
from ..auth import current_user
from ..models import ExpenseIn
from ..services import deny_staff_admin, get_club, wants_web, write_log
from ..util import fmt, month_of, now_iso, today_ist, uid

router = APIRouter(prefix="/clubs/{club_id}/expenses", tags=["expenses"])


@router.get("")
async def list_expenses(request: Request, club_id: str, month: str = "", user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    query = {"clubId": club["id"]}
    if month:
        rows = await db.expenses.find(query).to_list(None)
        rows = [e for e in rows if month_of(e.get("date", "")) == month]
    else:
        rows = await db.expenses.find(query, sort=[("date", -1)]).to_list(None)
    rows.sort(key=lambda e: (e.get("date", ""), e.get("createdAt", "")), reverse=True)
    by_cat = {}
    for e in rows:
        e.setdefault("refType", "menu_item" if e.get("auto") else "")  # web auto-stock badge
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + e["amount"]
    if wants_web(request):
        return rows  # React web ExpensesScreen expects a BARE ARRAY
    return {"rows": rows, "total": sum(e["amount"] for e in rows), "count": len(rows),
            "byCategory": by_cat, "month": month or month_of(today_ist()),
            "expenseCategories": [  # web alias — byCategory dict ka array form
                {"category": k, "amount": v}
                for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])]}


@router.post("", status_code=201)
async def create_expense(club_id: str, payload: ExpenseIn, user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    expense = {
        "id": uid("e"), "clubId": club["id"], "title": payload.title.strip(),
        "category": payload.category, "amount": payload.amount,
        "date": payload.date or today_ist(),
        "note": (payload.note or "").strip(), "createdAt": now_iso(), "auto": False,
        "refType": "",
    }
    await db.expenses.insert_one(expense)
    expense.pop("_id", None)
    await write_log(club["id"], "ADMIN",
                    f"Expense · {expense['title']} · ₹{fmt(expense['amount'])} [{expense['category']}]",
                    actor=user.get("name", ""))
    return expense


@router.delete("/{expense_id}")
async def delete_expense(club_id: str, expense_id: str, user: dict = Depends(current_user)):
    deny_staff_admin(user)
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    expense = await db.expenses.find_one({"id": expense_id, "clubId": club["id"]})
    if not expense:
        raise HTTPException(404, "Expense not found")
    await db.expenses.delete_one({"id": expense_id})
    await write_log(club["id"], "ADMIN",
                    f"Expense deleted · {expense['title']}", actor=user.get("name", ""))
    return {"ok": True, "message": f"Expense deleted · {expense['title']}"}
