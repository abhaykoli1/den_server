"""Test harness — in-memory DB (snapshot off), dev auth, tiny async client SDK."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["RD_SKIP_DOTENV"] = "1"  # never read backend/.env in tests
os.environ.setdefault("MONGODB_URI", "mongomock://test")
os.environ.setdefault("SNAPSHOT_DISABLED", "1")
os.environ.setdefault("AUTH_DEV_MODE", "true")
os.environ.setdefault("AUTH_DISABLED", "false")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789")
os.environ.setdefault("MASTER_ADMIN_EMAILS", "master@rowdys.dev")
os.environ["SMTP_HOST"] = ""       # mail engine stays in record-only mode
os.environ["GOOGLE_CLIENT_ID"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import db as db_mod  # noqa: E402
from app.main import app  # noqa: E402

CHECKS = {"n": 0}


def check(cond, label=""):
    CHECKS["n"] += 1
    assert cond, f"check failed: {label}"


def go(coro):
    return asyncio.run(coro)


class Api:
    """Minimal async test client with bearer auth helpers."""

    def __init__(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://test")
        self.token = None

    async def close(self):
        await self.client.aclose()

    def auth(self, token):
        self.token = token
        return self

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def get(self, url, params=None, headers=None):
        return await self.client.get(f"/api{url}", params=params,
                                     headers={**self._headers(), **(headers or {})})

    async def post(self, url, body=None):
        return await self.client.post(f"/api{url}", json=body or {}, headers=self._headers())

    async def patch(self, url, body=None):
        return await self.client.patch(f"/api{url}", json=body or {}, headers=self._headers())

    async def delete(self, url):
        return await self.client.delete(f"/api{url}", headers=self._headers())


@pytest.fixture(autouse=True)
def fresh_db():
    go(db_mod.reset_db_for_tests())


@pytest.fixture()
def api():
    c = Api()
    yield c
    go(c.close())


# ------------------------------------------------------------ scene builders
async def dev_login(api: Api, email: str, name: str = ""):
    r = await api.post("/auth/dev", {"email": email, "name": name})
    assert r.status_code == 200, r.text
    data = r.json()
    api.auth(data["token"])
    return data["user"]


async def master_api() -> Api:
    m = Api()
    await dev_login(m, "master@rowdys.dev", "Master Boss")
    return m


async def make_seller_plan(api: Api, master: Api, **over):
    body = {"name": "Pro", "description": "Full club", "price": 999,
            "billingCycle": "monthly", "durationDays": 30, "trialDays": 14,
            "maxClubs": 1, "features": ["All billing", "Reports"], "recommended": True,
            "sortOrder": 1}
    body.update(over)
    plan = (await master.post("/master/subscription-plans", body)).json()
    return plan


async def owner_with_club(api: Api, master: Api, plan_over=None):
    """Dev-login owner + (fresh) seller plan + select trial + auto club."""
    await dev_login(api, "owner@rowdys.dev", "Raju Bhai")
    plan = await make_seller_plan(api, master, **(plan_over or {}))
    r = await api.post("/account/subscription/select", {"planId": plan["id"]})
    assert r.status_code == 200, r.text
    r = await api.post("/clubs", {"name": "Rowdy's Den"})
    assert r.status_code == 201, r.text
    return api, r.json()


async def add_table(api: Api, club_id: str, name="Snooker 1", hourly=240, minc=20, **rate):
    body = {"name": name, "rate": {"hourlyRate": hourly, "minCharge": minc, **rate}}
    r = await api.post(f"/clubs/{club_id}/tables", body)
    assert r.status_code == 201, r.text
    return r.json()


async def add_member(api: Api, club_id: str, name="Ravi", phone="9800000001", email=""):
    r = await api.post(f"/clubs/{club_id}/members",
                       {"name": name, "phone": phone, "email": email})
    assert r.status_code == 201, r.text
    return r.json()
