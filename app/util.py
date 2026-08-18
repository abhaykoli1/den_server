"""Small shared helpers — ids, money, dates. Money is ALWAYS 2-dp rounded (decimal-exact rule)."""
import math
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
    IST = timezone(timedelta(hours=5, minutes=30))


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def money(x) -> float:
    """Round to 2dp, half-up. Never whole-rupee ceil."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(now_utc())


def parse_iso(s: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        raise ValueError(f"Bad date/time: {s}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def ist_date(iso_str: str) -> str:
    """YYYY-MM-DD in Asia/Kolkata (club-local day)."""
    return parse_iso(iso_str).astimezone(IST).date().isoformat()


def ist_month(iso_str: str) -> str:
    return ist_date(iso_str)[:7]


def ist_hour(iso_str: str) -> int:
    return parse_iso(iso_str).astimezone(IST).hour


def today_ist() -> str:
    return now_utc().astimezone(IST).date().isoformat()


def month_of(date_str: str) -> str:
    return date_str[:7]


def days_in_month(month: str) -> int:
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        nxt = datetime(y + 1, 1, 1)
    else:
        nxt = datetime(y, m + 1, 1)
    return (nxt - datetime(y, m, 1)).days


def ceil_minutes(started_iso: str, ended_iso: str) -> int:
    """Bill whole minutes, minimum 1."""
    secs = (parse_iso(ended_iso) - parse_iso(started_iso)).total_seconds()
    return max(1, math.ceil(secs / 60))


def split_evenly(total: float, n: int):
    """Split a money amount into n parts that sum EXACTLY back to total."""
    if n <= 1:
        return [money(total)]
    base = money(total / n)
    parts = [base] * (n - 1)
    parts.append(money(total - base * (n - 1)))
    return parts


def clean_phone(p: str) -> str:
    return "".join(ch for ch in str(p or "") if ch.isdigit())


def fmt(x) -> str:
    """₹ formatting without trailing .00 noise."""
    v = money(x)
    return str(int(v)) if v == int(v) else f"{v:.2f}"
