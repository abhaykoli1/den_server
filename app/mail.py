"""Mail engine — send + record, NEVER raises.

Without SMTP_HOST every mail is written to the `mailouts` collection with
sent:false (dev record-only mode). Templates are English, dark-branded with
the Rowdy's Den wordmark hosted at APP_PUBLIC_URL (base64/CID images get
stripped by Gmail — a hosted URL is reliable).
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import db as db_mod
from .config import settings
from .util import money, now_iso, uid, fmt


def _wrap(title: str, body_html: str) -> str:
    logo = (f'<img src="{settings.APP_PUBLIC_URL}/icons/logo1.png" width="220" '
            f'alt="Rowdy\'s Den" style="max-width:220px" />') if settings.APP_PUBLIC_URL else \
           '<div style="font-size:22px;font-weight:800;color:#f0f1f3">ROWDY&rsquo;S DEN</div>'
    return f"""
<div style="margin:0;padding:0;background:#0d0d0d;font-family:Inter,Arial,sans-serif">
  <div style="max-width:560px;margin:0 auto;padding:24px 14px">
    <div style="background:#111214;border:1px solid #26282e;border-radius:14px;overflow:hidden">
      <div style="padding:18px 20px;text-align:center;background:#0b0c0e">
        {logo}
        <div style="color:#8a8f99;font-size:11px;margin-top:6px;letter-spacing:.14em">
          BILLIARDS &middot; SNOOKER &middot; CAFE</div>
      </div>
      <div style="height:3px;background:linear-gradient(90deg,#e50f1f,#7a0a12)"></div>
      <div style="padding:20px">
        <h2 style="margin:0 0 10px;color:#f0f1f3;font-size:18px">{title}</h2>
        {body_html}
      </div>
      <div style="padding:14px 20px;border-top:1px solid #26282e;color:#6d727c;font-size:11px">
        Your club's accounts, in one place — zero hassle.
      </div>
    </div>
  </div>
</div>"""


def _rows(rows) -> str:
    trs = "".join(
        f'<tr><td style="padding:6px 0;color:#a8adb8;font-size:13px">{k}</td>'
        f'<td style="padding:6px 0;color:#f0f1f3;font-size:13px;text-align:right;font-weight:700">{v}</td></tr>'
        for k, v in rows)
    return f'<table style="width:100%;border-collapse:collapse;margin:10px 0">{trs}</table>'


def _note(text: str) -> str:
    return f'<p style="color:#a8adb8;font-size:13px;line-height:1.6">{text}</p>'


# ------------------------------------------------------------------- templates
def tpl_subscription(user_name: str, plan_name: str, status: str,
                     expires_at: str = "", price: float = 0):
    lead = {
        "trial": f"Congrats {user_name} — your free trial is live! 🎉",
        "active": f"Congrats {user_name} — your subscription is active! ✅",
        "pending": f"Hi {user_name} — your subscription request is in ⏳",
    }.get(status, f"Hi {user_name} — subscription update 🎱")
    rows = [("Plan", plan_name), ("Status", status.upper())]
    if price:
        rows.append(("Price", f"₹{fmt(price)}"))
    if expires_at:
        rows.append(("Renews / expires", expires_at[:10]))
    extra = ""
    if status == "pending":
        extra = _note("The Master Admin will activate your plan shortly after the payment "
                      "is confirmed. Your data stays safe meanwhile.")
    return f"Subscription {status} · {plan_name}", _wrap(lead, _rows(rows) + extra)


def tpl_plan_sold(club_name: str, member: dict, plan: dict):
    rows = [("Member", member["name"]), ("Plan", plan["name"]),
            ("Type", plan["type"].title()), ("Amount", f"₹{fmt(plan['amount'])}")]
    if plan.get("value"):
        rows.append(("Wallet value", f"₹{fmt(plan['value'])}"))
    if plan.get("frames"):
        frames = plan.get("frames")
        rows.append(("Frames", str(frames)))
    if plan.get("days"):
        rows.append(("Valid for", f"{plan['days']} days"))
    body = _rows(rows) + _note("Show this mail at the counter if anything looks off. "
                               "Your balance is always safe after renewal.")
    return f"Membership started · {plan['name']}", _wrap(f"Hi {member['name']} — membership started 🎱", body)


def tpl_balance_notify(club_name: str, member: dict):
    wallet = money(member.get("walletBalance") or 0)
    due = money(member.get("dueAmount") or 0)
    frames = member.get("passFramesLeft") or 0
    rows = [("Wallet balance", f"₹{fmt(wallet)}"),
            ("Frames left", str(frames)),
            ("Due pending", f"₹{fmt(due)}" if due else "No due — all clear ✅")]
    body = _rows(rows) + _note(
        f"A quick summary of your account at {club_name}. Pay dues at the counter "
        "whenever convenient.")
    return f"Your account summary · {club_name}", _wrap(f"Hi {member['name']} — your account 📋", body)


def tpl_plan_expired(club_name: str, member: dict):
    body = _rows([("Club", club_name), ("Plan", member.get("planName") or "Membership")]) + \
        _note("Your membership just expired. Renew at the counter to keep your discounts, "
              "frames and wallet benefits going.")
    return f"Membership expired · {club_name}", _wrap(f"Hi {member['name']} — time to renew ⏰", body)


# ------------------------------------------------------------------ send+record
async def send_and_record(kind: str, to: str, subject: str, html: str,
                          user_id: str = None, club_id: str = None,
                          member_id: str = None) -> dict:
    """Never raises. Writes a `mailouts` row for every attempt."""
    db = await db_mod.get_db()
    doc = {
        "id": uid("mail"), "kind": kind, "to": to, "subject": subject, "html": html,
        "sent": False, "createdAt": now_iso(),
    }
    if user_id:
        doc["userId"] = user_id
    if club_id:
        doc["clubId"] = club_id
    if member_id:
        doc["memberId"] = member_id
    if not to:
        doc["error"] = "no recipient email"
        await db.mailouts.insert_one(doc)
        return doc
    if not settings.SMTP_HOST:
        doc["error"] = "dev: SMTP not configured (recorded only)"
        await db.mailouts.insert_one(doc)
        return doc
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.MAIL_FROM
        msg["To"] = to
        msg.attach(MIMEText(html, "html", "utf-8"))
        if settings.SMTP_PORT == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ctx, timeout=15) as s:
                if settings.SMTP_USER:
                    s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.sendmail(settings.MAIL_FROM, [to], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                if settings.SMTP_USER:
                    s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.sendmail(settings.MAIL_FROM, [to], msg.as_string())
        doc["sent"] = True
        doc.pop("error", None)
    except Exception as exc:
        doc["error"] = f"{type(exc).__name__}: {exc}"[:300]
    await db.mailouts.insert_one(doc)
    return doc
