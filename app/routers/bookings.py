"""Table slot bookings — members reserve future IST slots, owners manage all.

Additive module (member-web v3): creates/cancels `bookings` docs only.
No billing, wallet, due or pass logic lives here — the counter still bills
played frames through the existing sessions/frames flow.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user, is_master
from ..models import In
from ..services import get_club
from ..util import IST, iso, now_iso, now_utc, uid

router = APIRouter(prefix="/clubs/{club_id}/bookings", tags=["bookings"])

MAX_DAYS_AHEAD = 14
MAX_ACTIVE_PER_MEMBER = 3
MIN_MINUTES = 30
MAX_MINUTES = 240
OPEN_HOUR = 8            # first slot may START at 08:00 IST
CLOSE_HOUR, CLOSE_MIN = 23, 30  # last slot must END by 23:30 IST

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class BookingIn(In):
    tableId: str
    date: str                      # YYYY-MM-DD, club-local (IST)
    start: str                     # HH:MM 24h IST
    end: str                       # HH:MM 24h IST
    players: int = 2
    notes: str = ""
    memberId: str | None = None    # owner/staff booking on someone's behalf


def _member_id(user: dict) -> str | None:
    return user.get("memberId") or (user.get("id") if user.get("role") == "member" else None)


def _clean(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _parse_slot(date_s: str, hm_s: str) -> datetime:
    """Club-local IST wall time -> aware datetime. 400 on any bad shape."""
    m = HM_RE.match((hm_s or "").strip())
    if not DATE_RE.match((date_s or "").strip()) or not m:
        raise HTTPException(400, "Pick a valid date and time (HH:MM, 24-hour)")
    hh, mm = int(m.group(1)), int(m.group(2))
    if mm not in (0, 30):
        raise HTTPException(400, "Slots start and end on the hour or half hour")
    try:
        day = datetime.strptime(date_s.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "That date doesn't look right (use YYYY-MM-DD)")
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


async def _sweep(db, club_id: str):
    """Lazy-complete past bookings on every read — no cron needed."""
    await db.bookings.update_many(
        {"clubId": club_id, "status": "upcoming", "endAt": {"$lt": now_iso()}},
        {"$set": {"status": "completed"}})


@router.get("")
async def list_bookings(club_id: str, mine: bool = False, table_id: str = "",
                        user: dict = Depends(current_user)):
    """Members always see only their own bookings; owner/staff see the club's."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    await _sweep(db, club["id"])
    query: dict = {"clubId": club["id"]}
    if user.get("role") == "member" or mine:
        mid = _member_id(user)
        if not mid:
            raise HTTPException(403, "This account is not linked to a club member profile")
        query["memberId"] = mid
    if table_id:
        query["tableId"] = table_id
    rows = [_clean(b) for b in await db.bookings.find(query).to_list(500)]
    rows.sort(key=lambda b: b.get("startAt", ""))
    return {"bookings": rows}


@router.post("", status_code=201)
async def create_booking(club_id: str, payload: BookingIn,
                         user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    role = user.get("role")
    if role == "member":
        mid = _member_id(user)
        member = await db.members.find_one({"id": mid, "clubId": club["id"]}) if mid else None
        if not member or member.get("active") is False:
            raise HTTPException(403, "This account is not linked to an active club membership")
        booker_mid, booker_name = member["id"], member.get("name", "Member")
    elif role in ("owner", "staff") or is_master(user):
        if payload.memberId:
            member = await db.members.find_one({"id": payload.memberId, "clubId": club["id"]})
            if not member:
                raise HTTPException(404, "Player not found")
            booker_mid, booker_name = member["id"], member.get("name", "Member")
        else:
            booker_mid, booker_name = user["id"], user.get("name", "Staff")
    else:
        raise HTTPException(403, "You don't have access to this club")

    table = await db.tables.find_one({"id": payload.tableId, "clubId": club["id"],
                                      "active": {"$ne": False}})
    if not table:
        raise HTTPException(404, "Table not found (or inactive)")

    start_local = _parse_slot(payload.date, payload.start)
    end_local = _parse_slot(payload.date, payload.end)
    if end_local <= start_local:
        raise HTTPException(400, "End time must be after start time")
    minutes = int((end_local - start_local).total_seconds() // 60)
    if minutes < MIN_MINUTES:
        raise HTTPException(400, f"Minimum slot is {MIN_MINUTES} minutes")
    if minutes > MAX_MINUTES:
        raise HTTPException(400, "Maximum slot is 4 hours — split longer sessions")
    if start_local.hour < OPEN_HOUR or (end_local.hour, end_local.minute) > (CLOSE_HOUR, CLOSE_MIN):
        raise HTTPException(400, "Slots run between 08:00 and 23:30")
    if start_local.astimezone(timezone.utc) <= now_utc():
        raise HTTPException(400, "That slot is in the past — pick a future time")
    days_out = (start_local.date() - now_utc().astimezone(IST).date()).days
    if days_out > MAX_DAYS_AHEAD:
        raise HTTPException(400, f"Bookings open only {MAX_DAYS_AHEAD} days ahead")

    start_iso = iso(start_local.astimezone(timezone.utc))
    end_iso = iso(end_local.astimezone(timezone.utc))

    existing = await db.bookings.find({"clubId": club["id"], "tableId": table["id"],
                                       "status": "upcoming"}).to_list(500)
    for other in existing:
        if other.get("startAt", "") < end_iso and other.get("endAt", "") > start_iso:
            raise HTTPException(
                400, f"That slot overlaps {other.get('memberName', 'another')} booking "
                     f"({other.get('startIST', '')}–{other.get('endIST', '')})")

    if role == "member":
        active = await db.bookings.count_documents({"clubId": club["id"], "memberId": booker_mid,
                                                    "status": "upcoming"})
        if active >= MAX_ACTIVE_PER_MEMBER:
            raise HTTPException(400, "You already have 3 upcoming bookings — cancel one to book again")

    booking = {
        "id": uid("bk"), "clubId": club["id"], "tableId": table["id"],
        "tableName": table.get("name", "Table"),
        "memberId": booker_mid, "memberName": booker_name,
        "dateIST": payload.date.strip(), "startIST": payload.start.strip(),
        "endIST": payload.end.strip(),
        "startAt": start_iso, "endAt": end_iso, "minutes": minutes,
        "players": max(1, min(8, int(payload.players or 2))),
        "notes": (payload.notes or "").strip()[:120],
        "status": "upcoming", "createdAt": now_iso(), "createdBy": user.get("name", ""),
    }
    await db.bookings.insert_one(booking)
    return {"booking": _clean(booking)}


@router.delete("/{booking_id}")
async def cancel_booking(club_id: str, booking_id: str,
                         user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    booking = await db.bookings.find_one({"id": booking_id, "clubId": club["id"]})
    if not booking:
        raise HTTPException(404, "Booking not found")
    if user.get("role") == "member" and booking.get("memberId") != _member_id(user):
        raise HTTPException(403, "You can only cancel your own bookings")
    if booking.get("status") != "upcoming":
        raise HTTPException(400, "Only upcoming bookings can be cancelled")
    if (booking.get("startAt") or "") <= now_iso():
        raise HTTPException(400, "This slot has already started — ask the counter")
    updated = await db.bookings.find_one_and_update(
        {"id": booking_id},
        {"$set": {"status": "cancelled", "cancelledAt": now_iso(),
                  "cancelledBy": user.get("name", "")}})
    return {"ok": True, "booking": _clean(updated)}
