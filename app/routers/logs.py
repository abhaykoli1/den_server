"""Activity logs feed — BILLING | PAYMENT | WARNING | ADMIN, member-scoped fetch."""
from typing import Optional

from fastapi import APIRouter, Depends

from .. import db as db_mod
from ..auth import current_user
from ..services import get_club

router = APIRouter(prefix="/clubs/{club_id}/logs", tags=["logs"])


@router.get("")
async def list_logs(club_id: str, tag: Optional[str] = None,
                    memberId: Optional[str] = None, limit: int = 200,
                    user: dict = Depends(current_user)):
    club = await get_club(user, club_id)
    db = await db_mod.get_db()
    query = {"clubId": club["id"]}
    if tag:
        query["tag"] = tag.upper()
    if memberId:
        query["memberId"] = memberId
    return await db.logs.find(query, sort=[("createdAt", -1)]).to_list(min(limit, 500))
