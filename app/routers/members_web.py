"""Rowdy's Den Members Web App — Jinja2-rendered SPA.

Serves the mobile members app as a Jinja2 template with API configuration
injected into the template. The app talks to the same FastAPI backend for
/api/auth/* and /api/member-portal/* endpoints.

Routes:
  GET  /members               → base.html (Jinja2 template with config)
  GET  /members/app.js        → app.js (JavaScript app logic)
  GET  /members/manifest.json → web app manifest
"""

import os
import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings

router = APIRouter(prefix="/members", tags=["members_web"])

# Setup Jinja2 environment for templates
templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "templates")
jinja_env = Environment(
    loader=FileSystemLoader(templates_dir),
    autoescape=select_autoescape(["html", "xml"])
)


def url_for(name, **kwargs):
    """Helper function to generate URLs for members app resources."""
    if name == 'members_web.manifest':
        return '/members/manifest.json'
    elif name == 'members_web.app_js':
        return '/members/app.js'
    elif name == 'static':
        path = kwargs.get('path', '')
        return f'/public{path}' if path.startswith('/') else f'/public/{path}'
    return name


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def members_app(request: Request):
    """Render the members app with Jinja2 configuration."""
    template = jinja_env.get_template("members/base.html")
    
    # Inject configuration into template
    html = template.render(
        api_base=settings.MEMBERS_WEB_API_BASE or "/api",
        google_client_id=settings.MEMBERS_WEB_GOOGLE_CLIENT_ID or "",
        club_timezone=settings.MEMBERS_WEB_CLUB_TIMEZONE or "Asia/Kolkata",
        tables_poll_ms=settings.MEMBERS_WEB_TABLES_POLL_MS or 20000,
        orders_poll_ms=settings.MEMBERS_WEB_ORDERS_POLL_MS or 45000,
        url_for=url_for,  # Pass the url_for helper to template
    )
    return html


@router.get("/app.js", include_in_schema=False)
async def app_js():
    """Serve the app.js file."""
    app_js_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "app.js"
    )
    if os.path.exists(app_js_path):
        return FileResponse(app_js_path, media_type="application/javascript")
    return JSONResponse({"error": "app.js not found"}, status_code=404)


@router.get("/manifest.json", include_in_schema=False)
async def manifest():
    """Serve the web app manifest."""
    manifest_data = {
        "name": "Rowdy's Den — Members",
        "short_name": "Den Members",
        "description": "Members app for Rowdy's Den clubs: live tables, wallet, food ordering, activity and frames.",
        "start_url": "/members",
        "scope": "/members",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#ffffff",
        "theme_color": "#111114",
        "icons": [
            {
                "src": "/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "/icons/icon-192-maskable.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable"
            }
        ],
        "categories": ["entertainment", "social"],
        "screenshots": [
            {
                "src": "/icons/screenshot-1.png",
                "sizes": "540x720",
                "type": "image/png",
                "form_factor": "narrow"
            }
        ]
    }
    return JSONResponse(manifest_data)
