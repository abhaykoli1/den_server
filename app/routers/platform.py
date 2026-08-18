"""Platform config — human-support contact (Rowdy Care handoff)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db as db_mod
from ..auth import current_user, master_user, is_master_email
from ..config import settings
from ..models import SupportPatchIn
from ..util import clean_phone, now_iso

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/support")
async def get_support(user: dict = Depends(current_user)):
    db = await db_mod.get_db()
    doc = await db.platform_config.find_one({"id": "support"})
    if doc:
        return {"email": doc.get("email", ""), "phone": doc.get("phone", "")}
    fallback = settings.MASTER_ADMIN_EMAILS[0] if settings.MASTER_ADMIN_EMAILS else ""
    return {"email": fallback, "phone": ""}


@router.patch("/support")
async def patch_support(payload: SupportPatchIn, user: dict = Depends(master_user)):
    email = payload.email.strip()
    digits = clean_phone(payload.phone)
    if email and ("@" not in email or "." not in email):
        raise HTTPException(400, "That email address doesn't look right")
    if payload.phone and len(digits) < 10:
        raise HTTPException(400, "That phone number doesn't look right — digits only, with country code")
    phone = f"+{digits}" if digits else ""
    db = await db_mod.get_db()
    doc = await db.platform_config.find_one_and_update(
        {"id": "support"},
        {"$set": {"email": email, "phone": phone, "updatedAt": now_iso()}},
        upsert=True)
    doc = await db.platform_config.find_one({"id": "support"})
    return {"email": doc.get("email", ""), "phone": doc.get("phone", "")}
