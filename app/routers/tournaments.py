"""Tournaments — knockout brackets + league round-robin, on-table timers with
server-side charges (loser pays), auto-advance, champions + auto prize expenses."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db as db_mod
from ..auth import current_user
from ..billing import resolve_hourly, table_amount
from ..models import (ParticipantIn, ParticipantPatchIn, PlayIn, ResultIn,
                      TournamentIn, TournamentPatchIn)
from ..services import (billing_gate, get_club, payment_log,
                        wants_web, write_log)
from ..tournament import (all_fixtures_settled, build_knockout, build_league,
                          finalize_played_match, league_standings)
from ..util import ceil_minutes, fmt, money, now_iso, today_ist, uid

router = APIRouter(prefix="/clubs/{club_id}/tournaments", tags=["tournaments"])


def _decorate(t: dict) -> dict:
    players = t.get("participants") or []
    matches = t.get("matches") or []
    t["playerCount"] = len(players)
    t["collected"] = money(sum((p.get("entryFee") or 0) for p in players
                               if p.get("paidEntry")))
    t["tableCharges"] = money(t.get("tableCharges") or 0)
    t["_standings"] = league_standings(players, matches) \
        if t.get("format") == "league" else []
    # web aliases (Flutter extra keys ignore karti hai)
    t["standings"] = t["_standings"]
    if t.get("format") == "league":
        t["bracket"] = len(players)
    else:
        n = max(1, len(players))
        t["bracket"] = 1 << max(0, (n - 1).bit_length())
    t.pop("_id", None)
    return t


def _respond(t: dict, request) -> dict:
    """Canonical (Flutter) match statuses: waiting/ready/table_live/done/bye.
    React web vocabulary alag hai: ready→pending, done→played."""
    out = _decorate(t)
    if wants_web(request):
        for m in out.get("matches") or []:
            if m.get("status") == "ready":
                m["status"] = "pending"
            elif m.get("status") == "done":
                m["status"] = "played"
    return out


@router.get("")
async def list_tournaments(request: Request, club_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    tours = await db.tournaments.find({"clubId": club["id"]}).to_list(None)
    tours.sort(key=lambda t: (t.get("date", ""), t.get("createdAt", "")), reverse=True)
    return [_decorate(t) for t in tours]


async def _get_tour(db, club_id: str, tid: str) -> dict:
    t = await db.tournaments.find_one({"id": tid, "clubId": club_id})
    if not t:
        raise HTTPException(404, "Tournament not found")
    return t


@router.post("", status_code=201)
async def create_tournament(request: Request, club_id: str, payload: TournamentIn,
                            user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    tour = {
        "id": uid("tour"), "clubId": club["id"], "name": payload.name.strip(),
        "game": payload.game.strip(), "date": payload.date,
        "entryFee": money(payload.entryFee), "prize1": money(payload.prize1),
        "prize2": money(payload.prize2), "maxPlayers": payload.maxPlayers,
        "tableRate": money(payload.tableRate), "format": payload.format,
            "notes": (payload.notes or "").strip(), "status": "upcoming",
        "participants": [], "matches": [], "tableCharges": 0.0,
        "winnerPid": None, "winnerName": None, "runnerUpName": None,
        "collected": 0.0, "completedAt": None, "createdAt": now_iso(),
    }
    await db.tournaments.insert_one(tour)
    return _respond(tour, request)


@router.patch("/{tour_id}")
async def patch_tournament(request: Request, club_id: str, tour_id: str, payload: TournamentPatchIn,
                           user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    ops = {k: v for k, v in payload.model_dump().items() if v is not None}
    # format is locked after creation (never in the patch model either)
    if t.get("status") in ("running", "completed"):
        for locked in ("entryFee", "maxPlayers", "tableRate", "date"):
            if locked in ops:
                raise HTTPException(400, f"Can't change {locked} after the tournament starts")
    if payload.status == "cancelled":
        ops["status"] = "cancelled"
    if not ops:
        raise HTTPException(400, "Nothing to update")
    t = await db.tournaments.find_one_and_update({"id": tour_id}, {"$set": ops})
    return _respond(t, request)


@router.delete("/{tour_id}")
async def delete_tournament(club_id: str, tour_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    res = await db.tournaments.delete_one({"id": tour_id, "clubId": club["id"]})
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(404, "Tournament not found")
    return {"ok": True, "message": "Tournament deleted"}


# ------------------------------------------------------------- participants
@router.post("/{tour_id}/participants", status_code=201)
async def add_participant(request: Request, club_id: str, tour_id: str, payload: ParticipantIn,
                          user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") != "upcoming":
        raise HTTPException(400, "Players can only be added before the start")
    players = t.get("participants") or []
    if len(players) >= t.get("maxPlayers", 64):
        raise HTTPException(400, f"Entry full — max {t.get('maxPlayers')} players")
    p = {"pid": uid("tp"), "name": payload.name.strip(),
         "phone": payload.phone.strip(), "memberId": payload.memberId,
         "paidEntry": bool(payload.paidEntry), "seed": len(players) + 1,
         "entryFee": t.get("entryFee", 0)}
    players.append(p)
    if p["paidEntry"] and (t.get("entryFee") or 0) > 0:  # web add-time paid entry
        await payment_log(club["id"], "tournaments", money(t["entryFee"]), payload.mode,
                          f"Tournament entry · {p['name']} · {t['name']}",
                          actor=user.get("name", ""), member_id=p.get("memberId"),
                          ref_type="tournament", ref_id=tour_id)
        await write_log(club["id"], "BILLING",
                        f"Entry fee collected · {p['name']} · ₹{fmt(t['entryFee'])} [{payload.mode}]",
                        actor=user.get("name", ""))
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"participants": players}})
    t["participants"] = players
    return _respond(t, request)


@router.post("/{tour_id}/participants/{pid}")  # web alias — mark-paid POSTs here
@router.patch("/{tour_id}/participants/{pid}")
async def patch_participant(request: Request, club_id: str, tour_id: str, pid: str,
                            payload: ParticipantPatchIn, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    players = list(t.get("participants") or [])
    p = next((x for x in players if x["pid"] == pid), None)
    if not p:
        raise HTTPException(404, "Player not found in this tournament")
    started = t.get("status") != "upcoming"
    if started and (payload.name is not None or payload.phone is not None):
        raise HTTPException(400, "Entries are locked once the tournament starts")
    if payload.name is not None:
        p["name"] = payload.name.strip()
    if payload.phone is not None:
        p["phone"] = payload.phone.strip()
    if payload.paidEntry is not None and payload.paidEntry != p.get("paidEntry"):
        if payload.paidEntry and (t.get("entryFee") or 0) > 0:
            await payment_log(club["id"], "tournaments", t["entryFee"],
                              payload.mode or "cash",  # web mark-paid sends its mode
                              f"Tournament entry · {p['name']} · {t['name']}",
                              actor=user.get("name", ""), member_id=p.get("memberId"),
                              ref_type="tournament", ref_id=tour_id)
        # no auto-refund on un-toggle (product rule)
        p["paidEntry"] = payload.paidEntry
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"participants": players}})
    t["participants"] = players
    return _respond(t, request)


@router.delete("/{tour_id}/participants/{pid}")
async def remove_participant(request: Request, club_id: str, tour_id: str, pid: str,
                             user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") != "upcoming":
        raise HTTPException(400, "Players can't be removed after the start")
    players = [x for x in (t.get("participants") or []) if x["pid"] != pid]
    if len(players) == len(t.get("participants") or []):
        raise HTTPException(404, "Player not found in this tournament")
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"participants": players}})
    t["participants"] = players
    return _respond(t, request)  # no auto-refund (product rule)


# ------------------------------------------------------------------- start
@router.post("/{tour_id}/start")
async def start_tournament(request: Request, club_id: str, tour_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") != "upcoming":
        raise HTTPException(400, "Tournament has already started")
    players = t.get("participants") or []
    if len(players) < 2:
        raise HTTPException(400, "Add at least 2 players to start")
    matches = build_knockout(players) if t.get("format") == "knockout" else build_league(players)
    t = await db.tournaments.find_one_and_update(
        {"id": tour_id}, {"$set": {"matches": matches, "status": "running"}})
    await write_log(club["id"], "BILLING",
                    f"Tournament started · {t['name']} · {len(players)} players · {t['format']}",
                    actor=user.get("name", ""))
    return _respond(t, request)


def _busy_table_ids(db_tours, sessions):
    busy = {s["tableId"] for s in sessions}
    for t in db_tours:
        for m in t.get("matches") or []:
            if m.get("status") == "table_live" and m.get("tableId"):
                busy.add(m["tableId"])
    return busy


@router.post("/{tour_id}/matches/{match_id}/play")
async def play_match(request: Request, club_id: str, tour_id: str, match_id: str, payload: PlayIn,
                     user: dict = Depends(current_user)):
    """▶ On Table — server timer starts; busy table -> 400."""
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") != "running":
        raise HTTPException(400, "Tournament isn't running")
    matches = list(t.get("matches") or [])
    m = next((x for x in matches if x["id"] == match_id), None)
    if not m:
        raise HTTPException(404, "Match not found")
    if m.get("status") != "ready":
        raise HTTPException(400, "Match isn't ready to go on a table")
    if not payload.tableId:  # web sends null when tracker is off — use score-only instead
        raise HTTPException(400, "Pick a table to time the match — or save a score-only result")
    table = await db.tables.find_one({"id": payload.tableId, "clubId": club["id"]})
    if not table or not table.get("active", True):
        raise HTTPException(404, "Table not found (or inactive)")
    sessions = await db.sessions.find({"clubId": club["id"]}).to_list(None)
    tours = await db.tournaments.find({"clubId": club["id"]}).to_list(None)
    if payload.tableId in _busy_table_ids(tours, sessions):
        raise HTTPException(400, f"{table['name']} is busy right now")
    hourly, peak = resolve_hourly(table["rate"], 2)
    m.update({"status": "table_live", "tableId": table["id"], "tableName": table["name"],
              "startedAt": now_iso(), "hourlyRate": hourly,
              "minCharge": float(table["rate"].get("minCharge") or 0), "peak": peak})
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"matches": matches}})
    t["matches"] = matches
    return _respond(t, request)


async def _complete_tournament(db, club_id: str, t: dict, matches: list, actor: str):
    """Champion + runner-up + prize expenses (auto, tournament category)."""
    players = t.get("participants") or []
    if t.get("format") == "league":
        table = league_standings(players, matches)
        champ = table[0] if table else None
        runner = table[1] if len(table) > 1 else None
    else:
        final = next((m for m in reversed(sorted(matches, key=lambda x: x["round"]))
                      if m.get("status") == "done"), None)
        champ = next((p for p in players if final and p["pid"] == final["winnerPid"]), None)
        if champ is None and final:
            champ = {"pid": final["winnerPid"], "name": next(
                (slot["name"] for slot in (final.get("p1"), final.get("p2"))
                 if slot and slot["pid"] == final["winnerPid"]), "?")}
        runner = None
        if final:
            rid = final.get("loserPid")
            runner = next((p for p in players if p["pid"] == rid),
                          {"pid": rid, "name": next(
                              (slot["name"] for slot in (final.get("p1"), final.get("p2"))
                               if slot and slot["pid"] == rid), "?")})
    updates = {"status": "completed", "completedAt": now_iso(),
               "winnerPid": (champ or {}).get("pid"),
               "winnerName": (champ or {}).get("name"),
               "runnerUpName": (runner or {}).get("name")}
    for key, prize, label in (("prize1", t.get("prize1") or 0, "1st"),
                              ("prize2", t.get("prize2") or 0, "2nd")):
        if prize and prize > 0:
            await db.expenses.insert_one({
                "id": uid("e"), "clubId": club_id,
                "title": f"Tournament prize · {t['name']} · {label}",
                "category": "tournament", "amount": money(prize), "date": today_ist(),
                "note": f"Auto expense — {t['name']} completed",
                "createdAt": now_iso(), "auto": True,
            })
    t2 = await db.tournaments.find_one_and_update({"id": t["id"]}, {"$set": updates})
    await write_log(club_id, "BILLING",
                    f"Tournament completed · {t['name']} · 🏆 {updates['winnerName']}",
                    actor=actor)
    return t2


@router.post("/{tour_id}/matches/{match_id}/result")
async def match_result(request: Request, club_id: str, tour_id: str, match_id: str, payload: ResultIn,
                       user: dict = Depends(current_user)):
    """Timer stops -> minutes; charge via tournament tableRate else the table rule;
    LOSER PAYS. Score-only (never went on a table) = no timer, no charge."""
    club = await get_club(user, club_id)
    await billing_gate(user, club)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") != "running":
        raise HTTPException(400, "Tournament isn't running")
    matches = list(t.get("matches") or [])
    m = next((x for x in matches if x["id"] == match_id), None)
    if not m:
        raise HTTPException(404, "Match not found")
    if m.get("status") not in ("ready", "table_live"):
        raise HTTPException(400, "Match already has a result")
    p1 = m.get("p1") or {}
    p2 = m.get("p2") or {}
    winner_pid = payload.winnerPid
    if not winner_pid and payload.winner in ("1", "2"):  # web alias: slot "1"/"2"
        winner_pid = p1.get("pid") if payload.winner == "1" else p2.get("pid")
    if winner_pid not in (p1.get("pid"), p2.get("pid")):
        raise HTTPException(400, "Winner must be one of the match players")
    loser_pid = p2["pid"] if winner_pid == p1.get("pid") else p1.get("pid")
    loser_name = p2["name"] if winner_pid == p1.get("pid") else p1.get("name")

    table_charge = 0.0
    if m.get("status") == "table_live" and m.get("startedAt"):
        m["endedAt"] = now_iso()
        m["minutes"] = ceil_minutes(m["startedAt"], m["endedAt"])
        if (t.get("tableRate") or 0) > 0:
            table_charge = money(t["tableRate"] / 60 * m["minutes"])
        else:
            table_charge = table_amount(float(m.get("hourlyRate", 0)), m["minutes"],
                                        float(m.get("minCharge") or 0))
    m.update({"status": "done", "score1": payload.score1, "score2": payload.score2,
              "winnerPid": winner_pid, "loserPid": loser_pid,
              "tableAmount": table_charge, "playedAt": now_iso()})
    if t.get("format") == "knockout":
        finalize_played_match(matches, m)

    if table_charge > 0:
        await payment_log(club["id"], "tournaments", table_charge, payload.mode,
                          f"Match table charge · {m['label']} · loser pays ({loser_name})",
                          actor=user.get("name", ""), ref_type="tournament",
                          ref_id=tour_id)
        t["tableCharges"] = money((t.get("tableCharges") or 0) + table_charge)
        await db.tournaments.update_one({"id": tour_id},
                                        {"$set": {"tableCharges": t["tableCharges"]}})
    await db.tournaments.update_one({"id": tour_id}, {"$set": {"matches": matches}})
    t["matches"] = matches

    final_done = t.get("format") == "knockout" and m["round"] == \
        max(x["round"] for x in matches) and m.get("status") == "done"
    if final_done or (t.get("format") == "league" and all_fixtures_settled(matches)):
        t = await _complete_tournament(db, club["id"], t, matches, user.get("name", ""))
    return _respond(t, request)


@router.post("/{tour_id}/cancel")
async def cancel_tournament(request: Request, club_id: str, tour_id: str, user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    t = await _get_tour(db, club["id"], tour_id)
    if t.get("status") == "completed":
        raise HTTPException(400, "A completed tournament can't be cancelled")
    t = await db.tournaments.find_one_and_update({"id": tour_id},
                                                 {"$set": {"status": "cancelled"}})
    await write_log(club["id"], "WARNING", f"Tournament cancelled · {t['name']}",
                    actor=user.get("name", ""))
    return _respond(t, request)
