"""Tournament engine — seeded single-elimination with byes + league (circle-method
round robin) with standings. Pure functions over plain dicts."""
import math

from .util import now_iso, uid

ROUND_NAMES = {1: "Final", 2: "Semi Final", 3: "Quarter Final",
               4: "Round of 16", 5: "Round of 32", 6: "Round of 64"}


def _slot(p):
    if not p:
        return None
    return {"pid": p["pid"], "name": p["name"], "seed": p.get("seed", 0)}


def _mk_match(round_no: int, label: str, idx: int, p1=None, p2=None):
    return {
        "id": uid("m"), "round": round_no, "label": label, "idx": idx,
        "p1": _slot(p1), "p2": _slot(p2),
        "score1": None, "score2": None,
        "winnerPid": None, "loserPid": None, "status": "waiting",
        "tableId": None, "tableName": None,
        "startedAt": None, "endedAt": None, "minutes": None,
        "tableAmount": 0, "playedAt": None,
    }


def _advance(matches, match, winner_pid):
    """Push a winner into the next-round match slot; flip to ready when full."""
    rounds = max(m["round"] for m in matches) + 1
    if match["round"] >= rounds - 1:
        return  # final — nothing to advance into
    nxt_idx = match["idx"] // 2
    slot = "p1" if match["idx"] % 2 == 0 else "p2"
    nxt = next((m for m in matches
                if m["round"] == match["round"] + 1 and m["idx"] == nxt_idx), None)
    if not nxt or not winner_pid:
        return
    if nxt[slot]:
        return
    source = match["p1"] if match["p1"] and match["p1"]["pid"] == winner_pid else match["p2"]
    nxt[slot] = dict(source) if source else None
    if nxt.get("p1") and nxt.get("p2") and nxt["status"] in ("waiting", "ready"):
        nxt["status"] = "ready"


def build_knockout(players: list) -> list:
    """players: participant dicts in seed order. Returns flat match list."""
    n = len(players)
    size = 2
    while size < n:
        size *= 2
    slots = players + [None] * (size - n)
    rounds = int(math.log2(size))
    matches = []
    for r in range(rounds):
        remaining = rounds - r
        name = ROUND_NAMES.get(remaining, f"Round of {2 ** remaining}")
        count = size // (2 ** (r + 1))
        for i in range(count):
            label = name if count == 1 else f"{name} · M{i + 1}"
            matches.append(_mk_match(r, label, i))
    # seed round 0: top vs bottom pairing
    r0 = [m for m in matches if m["round"] == 0]
    for i, m in enumerate(r0):
        a, b = slots[i], slots[size - 1 - i]
        m["p1"], m["p2"] = _slot(a), _slot(b)
        if a and b:
            m["status"] = "ready"
        elif a or b:
            m["status"] = "bye"
            m["winnerPid"] = (a or b)["pid"]
            m["playedAt"] = now_iso()
    # auto-advance bye winners (cascade)
    for m in r0:
        if m["status"] == "bye":
            _advance(matches, m, m["winnerPid"])
    return matches


def build_league(players: list) -> list:
    """Circle-method round robin; odd roster gets a ghost (bye fixtures)."""
    arr = list(players)
    if len(arr) % 2 == 1:
        arr.append(None)
    n = len(arr)
    cur = list(arr)
    matches = []
    for r in range(n - 1):
        for i in range(n // 2):
            a, b = cur[i], cur[n - 1 - i]
            label = f"Round {r + 1} · M{i + 1}"
            m = _mk_match(r, label, i, a, b)
            if a and b:
                m["status"] = "ready"
            else:
                real = a or b
                m["status"] = "bye"
                m["winnerPid"] = real["pid"] if real else None
                m["playedAt"] = now_iso()
            matches.append(m)
        cur = [cur[0]] + [cur[-1]] + cur[1:-1]  # rotate (0 stays fixed)
    return matches


def finalize_played_match(matches, match):
    """After a result: advance winner in knockout; no-op for league."""
    _advance(matches, match, match["winnerPid"])


def league_standings(players: list, matches: list) -> list:
    rows = {p["pid"]: {
        "pid": p["pid"], "name": p["name"], "played": 0, "won": 0, "lost": 0,
        "points": 0, "scoreFor": 0, "scoreAgainst": 0, "scoreDiff": 0,
    } for p in players}
    for m in matches:
        if m.get("status") != "done" or not m.get("winnerPid"):
            continue
        w, l = rows.get(m["winnerPid"]), rows.get(m.get("loserPid") or "")
        if not w or not l:
            continue
        s1 = m.get("score1") or 0
        s2 = m.get("score2") or 0
        w_score, l_score = (s1, s2) if m["p1"] and m["p1"]["pid"] == m["winnerPid"] else (s2, s1)
        w["played"] += 1; w["won"] += 1; w["points"] += 3
        w["scoreFor"] += w_score; w["scoreAgainst"] += l_score
        l["played"] += 1; l["lost"] += 1
        l["scoreFor"] += l_score; l["scoreAgainst"] += w_score
    table = list(rows.values())
    for r in table:
        r["scoreDiff"] = r["scoreFor"] - r["scoreAgainst"]
    table.sort(key=lambda r: (-r["points"], -r["scoreDiff"], -r["won"], r["name"]))
    return table


def all_fixtures_settled(matches: list) -> bool:
    return all(m.get("status") in ("done", "bye") for m in matches)
