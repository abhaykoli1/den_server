"""Rowdy's Den · Members App — server-rendered Jinja2 pages under `/members`.

Pages are rendered from a session cookie (`den_member`, the same JWT the API
uses). Every money-moving action goes through `/api/member-portal/*` with a
Bearer token (exposed to the page as `window.DEN.token`), so the JSON API stays
the single source of truth and cookies never authorise mutations (no CSRF).

Routes
  /members                    home            /members/wallet         wallet · dues · ledger
  /members/tables             live tables     /members/frames         my games + stats
  /members/cafe               cafe menu       /members/rankings       leaderboard
  /members/shop               pro shop        /members/bookings       list · calendar · new
  /members/item/{id}          product         /members/bookings/{id}  confirmation ticket
  /members/cart               cart+checkout   /members/membership     plans
  /members/orders[/{id}]      orders          /members/tournaments    events
  /members/activity           feed            /members/more · /profile
  /members/login (+ /email /verify /google /google-demo) · /members/logout
  /members/img/{item_id}      item photo (data-URL → bytes)   · /members/manifest.json
"""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import db as db_mod
from ..auth import (issue_token, member_profile_for_user, resolve_members_by_email,
                    upsert_user, verify_google_token)
from ..config import settings
from ..mail import send_and_record
from ..models import DevLoginIn
from ..portal import (catalog, club_tournaments, due_breakdown, frame_stats,
                      home_payload, live_tables, member_summary, membership_status,
                      my_activity, my_bills, my_bookings, my_frames, my_requests,
                      plan_view, public_club, public_item, sold_counts, wallet_ledger,
                      bill_view, emoji_for)
from ..util import IST, fmt, money, now_iso, now_utc, parse_iso, uid
from .auth import dev_login
from .bookings import CLOSE_HOUR, CLOSE_MIN, MAX_DAYS_AHEAD, OPEN_HOUR
from .member_portal import rankings as _rankings_api

router = APIRouter(prefix="/members", tags=["members-web"])

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
COOKIE = "den_member"
THEME_COOKIE = "den_theme"
APP_VERSION = "4.1.1"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(settings.MEMBERS_WEB_CLUB_TIMEZONE or "Asia/Kolkata")
except Exception:  # pragma: no cover
    TZ = IST


# ------------------------------------------------------------- jinja setup
def _inr(x, symbol="₹") -> str:
    """₹1,23,456.50 — Indian grouping, no trailing .00 noise."""
    try:
        v = money(x or 0)
    except Exception:
        return f"{symbol}0"
    neg = v < 0
    v = abs(v)
    whole = int(v)
    frac = round(v - whole, 2)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    out = s if frac == 0 else f"{s}.{int(round(frac * 100)):02d}"
    return f"{'-' if neg else ''}{symbol}{out}"


def _local(iso_s) -> Optional[datetime]:
    if not iso_s:
        return None
    try:
        return parse_iso(iso_s).astimezone(TZ)
    except Exception:
        return None


def _dt(iso_s, style: str = "full") -> str:
    d = _local(iso_s)
    if not d:
        return ""
    today = now_utc().astimezone(TZ).date()
    if style == "day":            # Jun 18
        return d.strftime("%b %d").replace(" 0", " ")
    if style == "chip_day":       # 18
        return d.strftime("%d").lstrip("0")
    if style == "chip_mon":       # JUN
        return d.strftime("%b").upper()
    if style == "time":           # 10:00 AM
        return d.strftime("%I:%M %p").lstrip("0")
    if style == "short_time":     # 10 AM / 10:30 AM
        return (d.strftime("%I %p") if d.minute == 0 else d.strftime("%I:%M %p")).lstrip("0")
    if style == "month":          # June 2025
        return d.strftime("%B %Y")
    if style == "date":           # 18 Jun 2025
        return d.strftime("%d %b %Y").lstrip("0")
    if style == "weekday":        # Wed
        return d.strftime("%a")
    if style == "rel":            # Today · Tomorrow · Wed, 18 Jun
        delta = (d.date() - today).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Tomorrow"
        if delta == -1:
            return "Yesterday"
        return d.strftime("%a, %d %b").replace(" 0", " ")
    if style == "ago":
        secs = int((now_utc() - d.astimezone(now_utc().tzinfo)).total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60} min ago"
        if secs < 86400:
            return f"{secs // 3600} hr ago"
        if secs < 86400 * 7:
            return f"{secs // 86400} d ago"
        return d.strftime("%d %b").lstrip("0")
    return d.strftime("%d %b %Y, %I:%M %p").lstrip("0").replace(", 0", ", ")


def _hm(hm: str, style: str = "short") -> str:
    """'18:30' → '6:30 PM' (booking IST wall times)."""
    try:
        h, m = (int(x) for x in hm.split(":"))
    except Exception:
        return hm or ""
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    if style == "short" and m == 0:
        return f"{h12} {suffix}"
    return f"{h12}:{m:02d} {suffix}"


def _initials(name: str) -> str:
    parts = [p for p in (name or "").replace("(", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _hue(name: str) -> int:
    return int(hashlib.md5((name or "x").encode()).hexdigest()[:4], 16) % 360


def _duration(minutes) -> str:
    try:
        m = int(minutes or 0)
    except Exception:
        return ""
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} hr" if r == 0 else f"{h} hr {r} min"


def _tojson(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")


jinja = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                    autoescape=select_autoescape(["html", "xml"]),
                    trim_blocks=True, lstrip_blocks=True)
jinja.filters.update({"inr": _inr, "dt": _dt, "hm": _hm, "initials": _initials, "hue": _hue,
                      "duration": _duration, "tojson_safe": _tojson, "fmt": fmt})
jinja.globals.update({"APP_VERSION": APP_VERSION, "emoji_for": emoji_for})


def render(request: Request, name: str, ctx: dict, status: int = 200) -> HTMLResponse:
    theme = request.cookies.get(THEME_COOKIE) or "light"
    base = {
        "request": request, "theme": theme if theme in ("light", "dark") else "light",
        "api_base": settings.MEMBERS_WEB_API_BASE or "/api",
        "google_client_id": settings.MEMBERS_WEB_GOOGLE_CLIENT_ID or settings.GOOGLE_CLIENT_ID or "",
        "tables_poll_ms": settings.MEMBERS_WEB_TABLES_POLL_MS or 20000,
        "orders_poll_ms": settings.MEMBERS_WEB_ORDERS_POLL_MS or 45000,
        "server_now": now_iso(), "path": request.url.path, "scheme": "club", "active_tab": "",
        "cart_badge": True,
    }
    base.update(ctx)
    html = jinja.get_template(name).render(**base)
    resp = HTMLResponse(html, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ----------------------------------------------------------- session/cookie
def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


def set_session(resp: Response, request: Request, token: str):
    resp.set_cookie(COOKIE, token, max_age=settings.JWT_EXPIRE_DAYS * 86400, httponly=True,
                    samesite="lax", secure=_secure(request), path="/")


def clear_session(resp: Response):
    resp.delete_cookie(COOKIE, path="/")


class _Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


async def _session_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"], issuer=settings.JWT_ISSUER)
    except Exception:
        return None
    db = await db_mod.get_db()
    user = await db.users.find_one({"id": payload.get("sub", "")})
    if not user or user.get("active") is False:
        return None
    profile = await member_profile_for_user(db, user)
    if not profile:
        return None
    profile["_token"] = token
    profile["_udoc"] = user
    return profile


async def page_ctx(request: Request):
    """(db, member, club, user) for the signed-in member, else redirect to login."""
    user = await _session_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": f"/members/login?next={quote(request.url.path)}"})
    member_id = user.get("memberId") or user.get("id")
    club_id = user.get("clubId")
    db = await db_mod.get_db()
    member = await db.members.find_one({"id": member_id, "active": {"$ne": False}}) if member_id else None
    club = await db.clubs.find_one({"id": club_id}) if club_id else None
    if not member or not club:
        raise HTTPException(status_code=303, headers={"Location": "/members/login?err=nomember"})
    return db, member, club, user


def _common(member: dict, club: dict, user: dict, unread: int = 0) -> dict:
    return {"member": member_summary(member), "club": public_club(club), "token": user.get("_token", ""),
            "email": user.get("email") or member.get("email") or "", "unread": unread,
            "memberships": [{"clubId": m.get("clubId"), "clubName": (m.get("club") or {}).get("name", "Club"),
                             "current": m.get("clubId") == club["id"]}
                            for m in (user.get("_udoc") or {}).get("memberships") or []]}


async def _unread(db, club, member, user) -> int:
    act = await my_activity(db, club, member, (user.get("_udoc") or {}).get("activitySeenAt"), limit=60)
    return act["unread"]


# ------------------------------------------------------------------- login
def _otp_enabled() -> bool:
    return settings.MEMBERS_WEB_LOGIN_OTP


def _otp_hash(email: str, code: str) -> str:
    return hmac.new(settings.JWT_SECRET.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request):
    db, member, club, user = await page_ctx(request)
    data = await home_payload(db, member, club, user.get("_udoc") or {})
    ctx = _common(member, club, user, data["unread"])
    ctx.update({"home": data, "active_tab": "home"})
    return render(request, "members/home.html", ctx)


def _google_configured() -> bool:
    return bool(settings.MEMBERS_WEB_GOOGLE_CLIENT_ID or settings.GOOGLE_CLIENT_ID)


async def _login_ctx(next: str, error: str = "", email: str = "") -> dict:
    """Shared context for every login-screen render (main page + error paths)."""
    ctx = {"next": next if next.startswith("/members") else "/members", "error": error, "email": email,
           "otp": _otp_enabled(), "dev_mode": settings.AUTH_DEV_MODE, "active_tab": "", "nav": False,
           "club": {}, "google_demo": bool(settings.AUTH_DEV_MODE), "demo_accounts": [],
           "google_configured": _google_configured()}
    db = await db_mod.get_db()
    ref = await db.members.find_one({})
    club_doc = await db.clubs.find_one({"id": ref.get("clubId")}) if ref else await db.clubs.find_one({})
    if club_doc:
        ctx["club"] = public_club(club_doc)
    if settings.AUTH_DEV_MODE and club_doc:
        ctx["demo_accounts"] = [{"name": m.get("name", ""), "email": m.get("email", "")}
                                for m in await db.members.find({"clubId": club_doc["id"], "email": {"$ne": ""}})
                                .to_list(2)]
    return ctx


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/members", err: str = ""):
    if await _session_user(request):
        return RedirectResponse("/members", status_code=303)
    msgs = {"nomember": "Your email isn't linked to a club member profile yet — ask the counter to add it to your player card.",
            "expired": "Your session expired — please sign in again.",
            "google": "Google sign-in could not be verified. Try again.",
            "staff": "This app is for club members. Owners and staff should use the billing app."}
    return render(request, "members/login.html", await _login_ctx(next, msgs.get(err, err)))


async def _finish_login(request: Request, email: str, next_url: str) -> Response:
    """Issue the member session (same users-doc flow as /api/auth/dev)."""
    res = await dev_login(DevLoginIn(email=email))
    user = res["user"]
    if user.get("role") != "member":
        return RedirectResponse("/members/login?err=staff", status_code=303)
    resp = RedirectResponse(next_url if next_url.startswith("/members") else "/members", status_code=303)
    set_session(resp, request, res["token"])
    return resp


@router.post("/login/email", include_in_schema=False)
async def login_email(request: Request, email: str = Form(...), next: str = Form("/members")):
    email = (email or "").strip().lower()
    db = await db_mod.get_db()
    members = await resolve_members_by_email(db, email) if "@" in email else []
    if not members:
        return render(request, "members/login.html",
                      await _login_ctx(next, "No membership found for that email. Ask the counter to add "
                                             "your email to your player profile, then try again.", email), status=400)
    if not _otp_enabled():
        return await _finish_login(request, email, next)
    # one-time code by email
    code = f"{secrets.randbelow(10**6):06d}"
    now = now_utc()
    await db.login_otps.delete_many({"email": email})
    await db.login_otps.insert_one({"id": uid("otp"), "email": email, "hash": _otp_hash(email, code),
                                    "expiresAt": (now + timedelta(minutes=10)).isoformat(), "attempts": 0,
                                    "createdAt": now_iso()})
    club_name = ((await db.clubs.find_one({"id": members[0].get("clubId")})) or {}).get("name", "Rowdy's Den")
    html = (f"<div style='font-family:sans-serif;font-size:16px'><p>Hi {members[0].get('name', 'there')},</p>"
            f"<p>Your {club_name} members app sign-in code is</p>"
            f"<p style='font-size:32px;letter-spacing:8px;font-weight:700'>{code}</p>"
            f"<p style='color:#666'>It expires in 10 minutes. If you didn't request it, ignore this mail.</p></div>")
    mail = await send_and_record("member_login_otp", email, f"{code} is your {club_name} sign-in code", html,
                                 club_id=members[0].get("clubId"), member_id=members[0].get("id"))
    dev_hint = code if (settings.AUTH_DEV_MODE and not mail.get("sent")) else ""
    if not mail.get("sent") and not dev_hint:
        return render(request, "members/login.html",
                      await _login_ctx(next, "We couldn't send the sign-in code right now. "
                                             "Please use Google sign-in or try later.", email), status=500)
    return render(request, "members/login_code.html", {"next": next, "email": email, "dev_hint": dev_hint, "nav": False,
                                                       "sent": bool(mail.get("sent"))})


@router.post("/login/verify", include_in_schema=False)
async def login_verify(request: Request, email: str = Form(...), code: str = Form(...), next: str = Form("/members")):
    email = (email or "").strip().lower()
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    db = await db_mod.get_db()
    rec = await db.login_otps.find_one({"email": email})
    err = ""
    if not rec:
        err = "Request a new code — that one isn't valid any more."
    elif rec.get("expiresAt", "") < now_utc().isoformat():
        err = "That code expired. Request a new one."
    elif int(rec.get("attempts") or 0) >= 5:
        err = "Too many attempts — request a new code."
    elif not hmac.compare_digest(rec.get("hash", ""), _otp_hash(email, code)):
        await db.login_otps.update_one({"id": rec["id"]}, {"$inc": {"attempts": 1}})
        err = "That code doesn't match. Check the email and try again."
    if err:
        return render(request, "members/login_code.html", {"next": next, "email": email, "error": err, "nav": False,
                                                           "sent": True}, status=400)
    await db.login_otps.delete_many({"email": email})
    return await _finish_login(request, email, next)


@router.post("/login/google", include_in_schema=False)
async def login_google(request: Request, credential: str = Form(...), next: str = Form("/members")):
    try:
        g = verify_google_token((credential or "").strip())
    except HTTPException:
        return RedirectResponse("/members/login?err=google", status_code=303)
    db = await db_mod.get_db()
    email = (g.get("email") or "").lower()
    if not await resolve_members_by_email(db, email):
        return RedirectResponse("/members/login?err=nomember", status_code=303)
    return await _finish_login(request, email, next)


async def _ensure_demo_member(db, email: str, name: str):
    """Dev-mode only: create a member profile in the demo club for an unlinked email."""
    ref = await db.members.find_one({})
    club = await db.clubs.find_one({"id": ref.get("clubId")}) if ref else await db.clubs.find_one({})
    if not club:
        raise HTTPException(503, "No club found to join — run the demo seed first")
    await db.members.insert_one({
        "id": uid("m"), "clubId": club["id"], "type": "regular", "active": True,
        "notes": "Joined via Google (demo)", "createdAt": now_iso(),
        "planId": None, "planExpiresAt": None, "tableDiscountPercent": None,
        "email": email, "name": (name or email.split("@")[0].title()).strip() or "New Member",
        "phone": "", "walletBalance": 0, "dueAmount": 0, "passFramesLeft": 0,
        "planName": None, "planType": None,
    })


@router.post("/login/google-demo", include_in_schema=False)
async def login_google_demo(request: Request):
    """Dev-mode stand-in for Google sign-in (shown on the login screen when no
    GOOGLE_CLIENT_ID is configured). Signs the email in as its member profile,
    creating a fresh demo member for unknown emails. JSON: {name, email}."""
    if not settings.AUTH_DEV_MODE:
        raise HTTPException(403, "Demo Google sign-in is only available in dev mode")
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = (str(body.get("email") or "")).strip().lower()
    name = (str(body.get("name") or "")).strip()
    if "@" not in email or "." not in email:
        raise HTTPException(400, "Enter a valid Google email")
    db = await db_mod.get_db()
    if not await resolve_members_by_email(db, email):
        await _ensure_demo_member(db, email, name)
    await upsert_user(db, email, name, google_sub=f"google-demo:{email}")
    return await _finish_login(request, email, "/members")


@router.get("/logout", include_in_schema=False)
@router.post("/logout", include_in_schema=False)
async def logout(request: Request):
    resp = RedirectResponse("/members/login", status_code=303)
    clear_session(resp)
    return resp


@router.post("/theme", include_in_schema=False)
async def set_theme(request: Request, theme: str = Form("light")):
    resp = JSONResponse({"ok": True, "theme": theme})
    resp.set_cookie(THEME_COOKIE, "dark" if theme == "dark" else "light", max_age=365 * 86400,
                    samesite="lax", secure=_secure(request), path="/")
    return resp


# -------------------------------------------------------------------- pages
@router.get("/tables", response_class=HTMLResponse, include_in_schema=False)
async def tables_page(request: Request):
    db, member, club, user = await page_ctx(request)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"tables": await live_tables(db, club, member["id"]), "active_tab": "home",
                "rules": {"open": OPEN_HOUR, "close": f"{CLOSE_HOUR}:{CLOSE_MIN:02d}"}})
    return render(request, "members/tables.html", ctx)


@router.get("/cafe", response_class=HTMLResponse, include_in_schema=False)
async def cafe_page(request: Request, q: str = "", cat: str = ""):
    db, member, club, user = await page_ctx(request)
    data = await catalog(db, club["id"])
    items = data["cafe"]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["name"].lower() or ql in i["category"].lower()]
    if cat:
        items = [i for i in items if i["category"].lower() == cat.lower()]
    groups: dict = {}
    for i in items:
        groups.setdefault(i["category"], []).append(i)
    popular = [i for i in data["popular"] if i["kind"] == "cafe"][:6]
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"items": items, "groups": groups, "categories": data["cafeCategories"], "popular": popular,
                "q": q, "cat": cat, "scheme": "cafe", "active_tab": "cafe",
                "tables": await live_tables(db, club, member["id"])})
    return render(request, "members/cafe.html", ctx)


@router.get("/shop", response_class=HTMLResponse, include_in_schema=False)
async def shop_page(request: Request, q: str = "", cat: str = "", tab: str = "new"):
    db, member, club, user = await page_ctx(request)
    data = await catalog(db, club["id"])
    items = data["shop"]
    all_hit = []
    if q:
        ql = q.lower()
        all_hit = [i for i in data["items"] if ql in i["name"].lower() or ql in i["category"].lower()]
        items = [i for i in items if ql in i["name"].lower() or ql in i["category"].lower()]
    if cat:
        items = [i for i in items if i["category"].lower() == cat.lower()]
    groups: dict = {}
    for i in items:
        groups.setdefault(i["category"], []).append(i)
    popular = [i for i in data["popular"] if i["kind"] == "shop"][:6]
    newest = [i for i in data["newArrivals"] if i["kind"] == "shop"][:6]
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"items": items, "groups": groups, "categories": data["shopCategories"], "popular": popular,
                "newest": newest, "q": q, "cat": cat, "tab": tab, "active_tab": "shop",
                "search_hits": all_hit})
    return render(request, "members/shop.html", ctx)


@router.get("/item/{item_id}", response_class=HTMLResponse, include_in_schema=False)
async def item_page(request: Request, item_id: str):
    db, member, club, user = await page_ctx(request)
    item = await db.menu_items.find_one({"id": item_id, "clubId": club["id"], "active": {"$ne": False}})
    if not item:
        raise HTTPException(404, "Item not found")
    sold = await sold_counts(db, club["id"])
    view = public_item(item, sold)
    data = await catalog(db, club["id"], include_out_of_stock=False)
    related = [v for v in data["items"] if v["id"] != item_id and v["category"] == view["category"]][:6]
    if len(related) < 4:
        related += [v for v in data["items"] if v["id"] != item_id and v["kind"] == view["kind"] and v not in related][:6 - len(related)]
    ctx = _common(member, club, user)
    ctx.update({"item": view, "related": related, "scheme": "paper", "active_tab": "cafe" if view["kind"] == "cafe" else "shop",
                "tables": await live_tables(db, club, member["id"])})
    return render(request, "members/product.html", ctx)


@router.get("/cart", response_class=HTMLResponse, include_in_schema=False)
async def cart_page(request: Request):
    db, member, club, user = await page_ctx(request)
    data = await catalog(db, club["id"])
    frames = await my_frames(db, club, member["id"], limit=100)
    bills = await my_bills(db, club, member["id"], limit=100)
    ctx = _common(member, club, user)
    ctx.update({"catalog_items": data["items"], "tables": await live_tables(db, club, member["id"]),
                "dues": due_breakdown(member, frames, bills), "scheme": "paper", "active_tab": "shop",
                "cart_badge": False})
    return render(request, "members/cart.html", ctx)


@router.get("/orders", response_class=HTMLResponse, include_in_schema=False)
async def orders_page(request: Request, tab: str = "all"):
    db, member, club, user = await page_ctx(request)
    bills = await my_bills(db, club, member["id"])
    groups: dict = {}
    for b in bills:
        key = _dt(b["createdAt"], "month")
        groups.setdefault(key, []).append(b)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"orders": bills, "groups": groups, "tab": tab, "active_tab": "more",
                "unpaid": [b for b in bills if b["dueAmount"] > 0]})
    return render(request, "members/orders.html", ctx)


@router.get("/orders/{order_id}", response_class=HTMLResponse, include_in_schema=False)
async def order_page(request: Request, order_id: str, placed: int = 0):
    db, member, club, user = await page_ctx(request)
    bill = await db.item_bills.find_one({"id": order_id, "clubId": club["id"], "memberId": member["id"]})
    if not bill:
        raise HTTPException(404, "Order not found")
    ctx = _common(member, club, user)
    ctx.update({"order": bill_view(bill), "placed": bool(placed), "scheme": "paper", "active_tab": "more",
                "share_url": str(request.url).split("?")[0]})
    return render(request, "members/order_detail.html", ctx)


@router.get("/wallet", response_class=HTMLResponse, include_in_schema=False)
async def wallet_page(request: Request, tab: str = "all"):
    db, member, club, user = await page_ctx(request)
    frames = await my_frames(db, club, member["id"])
    bills = await my_bills(db, club, member["id"])
    plans = await db.plans.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    reqs = await my_requests(db, club, member["id"])
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"dues": due_breakdown(member, frames, bills),
                "ledger": await wallet_ledger(db, club, member, frames, bills),
                "offers": [plan_view(p, member) for p in plans if p.get("type") == "wallet"],
                "requests": reqs, "membership": membership_status(member), "tab": tab,
                "unpaid_orders": [b for b in bills if b["dueAmount"] > 0], "active_tab": "more",
                "qr": club.get("qrCode") or ""})
    return render(request, "members/wallet.html", ctx)


@router.get("/frames", response_class=HTMLResponse, include_in_schema=False)
async def frames_page(request: Request, tab: str = "all"):
    db, member, club, user = await page_ctx(request)
    frames = await my_frames(db, club, member["id"])
    groups: dict = {}
    for f in frames:
        groups.setdefault(_dt(f.get("endedAt") or f.get("createdAt"), "month"), []).append(f)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"frames": frames, "groups": groups, "stats": frame_stats(frames), "tab": tab, "active_tab": "more"})
    return render(request, "members/frames.html", ctx)


@router.get("/rankings", response_class=HTMLResponse, include_in_schema=False)
async def rankings_page(request: Request):
    db, member, club, user = await page_ctx(request)
    data = await _rankings_api(user)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"rows": data["rankings"], "me": data["me"], "active_tab": "more"})
    return render(request, "members/rankings.html", ctx)


@router.get("/bookings", response_class=HTMLResponse, include_in_schema=False)
async def bookings_page(request: Request, tab: str = "list", table: str = "", date: str = ""):
    db, member, club, user = await page_ctx(request)
    rows = await my_bookings(db, club, member["id"])
    upcoming = sorted([b for b in rows if b.get("status") == "upcoming"], key=lambda b: b.get("startAt", ""))
    past = [b for b in rows if b.get("status") != "upcoming"]
    groups: dict = {}
    for b in upcoming + past:
        groups.setdefault(_dt(b["startAt"], "month"), []).append(b)
    today = now_utc().astimezone(TZ).date()
    days = []
    for i in range(0, MAX_DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        days.append({"iso": d.isoformat(), "dow": d.strftime("%a"), "day": d.day,
                     "label": "Today" if i == 0 else ("Tmrw" if i == 1 else d.strftime("%a"))})
    booked_days = sorted({b.get("dateIST") for b in rows if b.get("status") == "upcoming"})
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"bookings": rows, "upcoming": upcoming, "past": past, "groups": groups, "tab": tab,
                "tables": await live_tables(db, club, member["id"]), "days": days, "today": today.isoformat(),
                "preselect_table": table, "preselect_date": date, "booked_days": booked_days,
                "rules": {"openHour": OPEN_HOUR, "closeHour": CLOSE_HOUR, "closeMin": CLOSE_MIN, "maxDays": MAX_DAYS_AHEAD},
                "active_tab": "bookings"})
    return render(request, "members/bookings.html", ctx)


@router.get("/bookings/{booking_id}.ics", include_in_schema=False)
async def booking_ics(request: Request, booking_id: str):
    db, member, club, user = await page_ctx(request)
    b = await db.bookings.find_one({"id": booking_id, "clubId": club["id"], "memberId": member["id"]})
    if not b:
        raise HTTPException(404, "Booking not found")
    def z(s):
        return parse_iso(s).strftime("%Y%m%dT%H%M%SZ")
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Rowdys Den//Members//EN", "BEGIN:VEVENT",
        f"UID:{b['id']}@rowdysden", f"DTSTAMP:{z(now_iso())}", f"DTSTART:{z(b['startAt'])}", f"DTEND:{z(b['endAt'])}",
        f"SUMMARY:{club.get('name', 'Club')} · {b.get('tableName', 'Table')}",
        f"DESCRIPTION:Table booking for {b.get('players', 2)} players. Booking no. {b['id'][-6:].upper()}",
        f"LOCATION:{club.get('name', '')}", "END:VEVENT", "END:VCALENDAR", ""])
    return Response(ics, media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="booking-{b["id"][-6:]}.ics"'})


@router.get("/bookings/{booking_id}", response_class=HTMLResponse, include_in_schema=False)
async def booking_page(request: Request, booking_id: str, new: int = 0):
    db, member, club, user = await page_ctx(request)
    b = await db.bookings.find_one({"id": booking_id, "clubId": club["id"], "memberId": member["id"]})
    if not b:
        raise HTTPException(404, "Booking not found")
    b.pop("_id", None)
    table = await db.tables.find_one({"id": b.get("tableId")}) or {}
    rate = (table.get("rate") or {})
    ctx = _common(member, club, user)
    ctx.update({"b": b, "is_new": bool(new), "scheme": "paper", "active_tab": "bookings",
                "rate": money(rate.get("hourlyRate") or 0), "share_url": str(request.url).split("?")[0],
                "can_cancel": b.get("status") == "upcoming" and (b.get("startAt") or "") > now_iso()})
    return render(request, "members/booking_detail.html", ctx)


@router.get("/membership", response_class=HTMLResponse, include_in_schema=False)
async def membership_page(request: Request):
    db, member, club, user = await page_ctx(request)
    plans = await db.plans.find({"clubId": club["id"], "active": {"$ne": False}}).to_list(None)
    plans.sort(key=lambda p: (p.get("type") != "monthly", p.get("type") != "pass", money(p.get("amount") or 0)))
    reqs = await my_requests(db, club, member["id"])
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"plans": [plan_view(p, member) for p in plans], "membership": membership_status(member),
                "requests": reqs["membership"], "qr": club.get("qrCode") or "", "active_tab": "more"})
    return render(request, "members/membership.html", ctx)


@router.get("/tournaments", response_class=HTMLResponse, include_in_schema=False)
async def tournaments_page(request: Request, tab: str = "upcoming"):
    db, member, club, user = await page_ctx(request)
    tours = await club_tournaments(db, club, member["id"])
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"tours": tours, "tab": tab, "active_tab": "more",
                "upcoming": [t for t in tours if t["status"] in ("upcoming", "running")],
                "past": [t for t in tours if t["status"] not in ("upcoming", "running")]})
    return render(request, "members/tournaments.html", ctx)


@router.get("/activity", response_class=HTMLResponse, include_in_schema=False)
async def activity_page(request: Request):
    db, member, club, user = await page_ctx(request)
    act = await my_activity(db, club, member, (user.get("_udoc") or {}).get("activitySeenAt"))
    groups: dict = {}
    for a in act["items"]:
        groups.setdefault(_dt(a["at"], "rel") if _dt(a["at"], "rel") in ("Today", "Yesterday") else _dt(a["at"], "date"), []).append(a)
    await db.users.update_one({"id": user.get("id")}, {"$set": {"activitySeenAt": now_iso()}})
    ctx = _common(member, club, user, 0)
    ctx.update({"activity": act["items"], "groups": groups, "active_tab": "more"})
    return render(request, "members/activity.html", ctx)


@router.get("/more", response_class=HTMLResponse, include_in_schema=False)
async def more_page(request: Request):
    db, member, club, user = await page_ctx(request)
    frames = await my_frames(db, club, member["id"], limit=200)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"stats": frame_stats(frames), "membership": membership_status(member), "active_tab": "more",
                "tables_count": len(await live_tables(db, club))})
    return render(request, "members/more.html", ctx)


@router.get("/profile", response_class=HTMLResponse, include_in_schema=False)
async def profile_page(request: Request):
    db, member, club, user = await page_ctx(request)
    ctx = _common(member, club, user, await _unread(db, club, member, user))
    ctx.update({"membership": membership_status(member), "active_tab": "more",
                "tables": await live_tables(db, club, member["id"])})
    return render(request, "members/profile.html", ctx)


# -------------------------------------------------------------- item image
@router.get("/img/{item_id}", include_in_schema=False)
async def item_image(item_id: str, request: Request):
    db = await db_mod.get_db()
    item = await db.menu_items.find_one({"id": item_id})
    img = (item or {}).get("image") or ""
    if not img.startswith("data:"):
        raise HTTPException(404, "No image")
    try:
        head, b64 = img.split(",", 1)
        mime = head[5:].split(";")[0] or "image/jpeg"
        data = base64.b64decode(b64)
    except Exception:
        raise HTTPException(404, "Bad image")
    etag = hashlib.md5(data).hexdigest()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    return Response(data, media_type=mime, headers={"Cache-Control": "public, max-age=86400", "ETag": etag})


@router.get("/manifest.json", include_in_schema=False)
async def manifest():
    return JSONResponse({
        "name": "Rowdy's Den — Members", "short_name": "Den Members",
        "description": "Your club in your pocket: live tables, bookings, cafe & pro-shop orders, wallet, frames and events.",
        "start_url": "/members", "scope": "/members", "display": "standalone", "orientation": "portrait-primary",
        "background_color": "#bfd1dc", "theme_color": "#bfd1dc",
        "icons": [{"src": "/members/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/members/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/members/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "categories": ["sports", "food", "lifestyle"],
    })
