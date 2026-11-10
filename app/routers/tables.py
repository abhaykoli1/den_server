"""Tables CRUD + toggle-active, with peak-pricing and glove-price fields."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user
from ..models import TableIn, TableLightIn, TablePatchIn
from ..services import billing_gate, get_club, write_log
from ..util import now_iso, uid

router = APIRouter(prefix="/clubs/{club_id}/tables", tags=["tables"])


def _rate_dict(rate) -> dict:
    r = {
        "hourlyRate": float(rate.hourlyRate),
        "billingMode": rate.billingMode,
        "ratesByPlayers": {str(k): float(v) for k, v in (rate.ratesByPlayers or {}).items()},
        "minCharge": float(rate.minCharge or 0),
        "glovePrice": float(rate.glovePrice or 0),
    }
    if rate.peakHourlyRate:
        if rate.peakStartHour is None or rate.peakEndHour is None:
            raise HTTPException(400, "Set both peak start and end hours (0–23)")
        if rate.peakStartHour == rate.peakEndHour:
            raise HTTPException(400, "Peak start and end hours can't be the same")
        r["peakHourlyRate"] = float(rate.peakHourlyRate)
        r["peakStartHour"] = rate.peakStartHour
        r["peakEndHour"] = rate.peakEndHour
    else:
        r["peakHourlyRate"] = None
        r["peakStartHour"] = None
        r["peakEndHour"] = None
    return r


@router.get("")
async def list_tables(club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    tables = await db.tables.find({"clubId": club["id"]}).to_list(None)
    tables.sort(key=lambda t: (t.get("sortOrder", 0), t.get("name", "")))
    return tables


@router.post("", status_code=201)
async def create_table(club_id: str, payload: TableIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    table = {
        "id": uid("t"), "clubId": club["id"], "name": payload.name.strip(),
        "sport": payload.sport, "lightEnabled": payload.lightEnabled,
        "lightState": payload.lightState,
        "active": True, "sortOrder": payload.sortOrder or 0,
        "rate": _rate_dict(payload.rate),
    }
    await db.tables.insert_one(table)
    table.pop("_id", None)
    return table


@router.patch("/{table_id}")
async def patch_table(club_id: str, table_id: str, payload: TablePatchIn,
                      user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    current = await db.tables.find_one({"id": table_id, "clubId": club["id"]})
    if not current:
        raise HTTPException(404, "Table not found")
    ops = {}
    if payload.name is not None:
        ops["name"] = payload.name.strip()
    if payload.sport is not None:
        ops["sport"] = payload.sport
    if payload.lightEnabled is not None:
        ops["lightEnabled"] = payload.lightEnabled
    if payload.lightState is not None:
        light_enabled = payload.lightEnabled if payload.lightEnabled is not None else current.get("lightEnabled", False)
        if payload.lightState == "on" and not light_enabled:
            raise HTTPException(400, "Enable smart light control for this table first")
        ops["lightState"] = payload.lightState
    if payload.rate is not None:
        ops["rate"] = _rate_dict(payload.rate)
    if payload.sortOrder is not None:
        ops["sortOrder"] = payload.sortOrder
    if payload.active is not None:
        ops["active"] = payload.active
    if not ops:
        raise HTTPException(400, "Nothing to update")
    table = await db.tables.find_one_and_update(
        {"id": table_id, "clubId": club["id"]}, {"$set": ops})
    return table


@router.post("/{table_id}/light")
async def control_light(club_id: str, table_id: str, payload: TableLightIn,
                        user: dict = Depends(current_user)):
    """Demo-ready smart-light control; persists state for a controller bridge."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    table = await db.tables.find_one({"id": table_id, "clubId": club["id"]})
    if not table:
        raise HTTPException(404, "Table not found")
    if not table.get("lightEnabled"):
        raise HTTPException(400, "Enable smart light control for this table first")
    state = ("off" if table.get("lightState", "off") == "on" else "on") \
        if payload.state == "toggle" else payload.state
    await db.tables.update_one(
        {"id": table_id, "clubId": club["id"]},
        {"$set": {"lightState": state, "lightUpdatedAt": now_iso(),
                   "lightUpdatedBy": user.get("name", "")}},
    )
    await write_log(club["id"], "ADMIN", f"Table light {state.upper()} · {table['name']}",
                    actor=user.get("name", ""))
    return {"tableId": table_id, "state": state, "mode": "simulated"}


@router.post("/{table_id}/toggle-active")
async def toggle_table(club_id: str, table_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    table = await db.tables.find_one({"id": table_id, "clubId": club["id"]})
    if not table:
        raise HTTPException(404, "Table not found")
    table = await db.tables.find_one_and_update(
        {"id": table_id}, {"$set": {"active": not table.get("active", True)}})
    return table


@router.delete("/{table_id}")
async def delete_table(club_id: str, table_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    live = await db.sessions.find_one({"clubId": club["id"], "tableId": table_id})
    if live:
        raise HTTPException(400, "That table has an active session — stop it first")
    res = await db.tables.delete_one({"id": table_id, "clubId": club["id"]})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(404, "Table not found")
    return {"ok": True, "message": "Table deleted"}
