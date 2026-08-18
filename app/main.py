"""Rowdy's Den — Club Billing · FastAPI app entrypoint.

HTTP contract: 200/201 ok · 400 readable · 401 auth · 402 subscription lock ·
403 role/tenant/admin-area · 404 · 500 clean JSON (no stack traces) · 422 → 400.
"""
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import dump_snapshot, get_db
from .routers import (auth, clubs, expenses, frames, items, logs, master,
                      members, plans, platform, reports, sessions,
                      subscriptions, tables, team, tournaments)

app = FastAPI(title="Rowdy's Den — Club Billing API",
              version="3.22.1", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    await get_db()  # hydrates snapshot + ensures indexes


@app.on_event("shutdown")
async def _shutdown():
    dump_snapshot()


@app.exception_handler(RequestValidationError)
async def validation_handler(_req: Request, exc: RequestValidationError):
    err = exc.errors()[0] if exc.errors() else {}
    loc = " → ".join(str(x) for x in err.get("loc", []) if x not in ("body",))
    msg = err.get("msg", "Invalid request").replace("Value error, ", "")
    text = f"{loc}: {msg}" if loc else msg
    return JSONResponse(status_code=400, content={"detail": text})


@app.exception_handler(Exception)
async def unhandled_handler(_req: Request, exc: Exception):
    traceback.print_exception(exc)
    return JSONResponse(status_code=500,
                        content={"detail": "Something went wrong — please try again"})


@app.get("/")
async def root():
    return {"name": "Rowdy's Den — Club Billing API", "version": "3.22.1",
            "docs": "/docs", "health": "/api/health"}


@app.get("/api/health")
async def health():
    return {"ok": True, "db": "mongomock" if settings.is_mock_db else "mongo",
            "authDevMode": settings.AUTH_DEV_MODE}


API_PREFIX = "/api"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(subscriptions.router, prefix=API_PREFIX)
app.include_router(clubs.router, prefix=API_PREFIX)
app.include_router(tables.router, prefix=API_PREFIX)
app.include_router(members.router, prefix=API_PREFIX)
app.include_router(plans.router, prefix=API_PREFIX)
app.include_router(sessions.router, prefix=API_PREFIX)
app.include_router(frames.router, prefix=API_PREFIX)
app.include_router(logs.router, prefix=API_PREFIX)
app.include_router(items.router, prefix=API_PREFIX)
app.include_router(expenses.router, prefix=API_PREFIX)
app.include_router(reports.router, prefix=API_PREFIX)
app.include_router(tournaments.router, prefix=API_PREFIX)
app.include_router(team.router, prefix=API_PREFIX)
app.include_router(master.router, prefix=API_PREFIX)
app.include_router(platform.router, prefix=API_PREFIX)
