"""Environment configuration. Source of truth only has placeholders — see .env.example.

Loads `backend/.env` if present (setdefault semantics — real environment
variables ALWAYS win, so tests/CI/Vercel envs are never overridden by the file).
"""
import os

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def _load_dotenv(path: str = _ENV_PATH):
    if os.environ.get("RD_SKIP_DOTENV"):  # tests/CI always win over the file
        return
    try:
        with open(path) as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key and val and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


_load_dotenv()


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _clean_mongo_uri(v: str) -> str:
    """Dashboard/Vercel paste accidents ko seedha normalize karo:
    leading/trailing whitespace+newlines, surrounding quotes, aur host-list me
    extra/empty comma (Atlas SRV single-host hota hai — 'a,,b' ya 'a,' dono
    crash karta tha: 'Empty host (or extra comma in host list)')."""
    v = (v or "").strip().strip('"').strip("'").strip()
    if v.startswith(("mongodb://", "mongodb+srv://")) and "," in v:
        scheme, _, rest = v.partition("://")
        hosts, slash, path = rest.partition("/")
        cleaned = ",".join(h for h in (x.strip() for x in hosts.split(",")) if h)
        v = f"{scheme}://{cleaned}{('/' + path) if slash else ''}"
    return v


class Settings:
    # --- database
    MONGODB_URI: str = _clean_mongo_uri(os.environ.get("MONGODB_URI", "mongomock://demo"))
    MONGODB_DB: str = os.environ.get("MONGODB_DB", "rowdys_den")

    # --- auth
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-secret-change-me-0123456789abcdef!!")
    JWT_ISSUER: str = os.environ.get("JWT_ISSUER", "rowdys-den")
    JWT_EXPIRE_DAYS: int = int(os.environ.get("JWT_EXPIRE_DAYS", "14"))
    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    # Comma-separated EXTRA OAuth client IDs (e.g. Flutter app Android/iOS clients).
    # The web client above stays required for the React frontend; extras are additive.
    GOOGLE_CLIENT_IDS = [
        c.strip()
        for c in os.environ.get("GOOGLE_CLIENT_IDS", "").split(",")
        if c.strip()
    ]
    AUTH_DEV_MODE: bool = _flag("AUTH_DEV_MODE", True)
    AUTH_DISABLED: bool = _flag("AUTH_DISABLED", False)
    MASTER_ADMIN_EMAILS = [
        e.strip().lower()
        for e in os.environ.get("MASTER_ADMIN_EMAILS", "master@rowdys.dev").split(",")
        if e.strip()
    ]

    # --- http
    # Default: the React web app runs on Vite (localhost:5173). Prod sets CORS_ORIGINS env.
    CORS_ORIGINS = [
        o.strip()
        for o in os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()
    ]

    # --- dev snapshot persistence (mongomock dev only)
    DATA_DIR: str = os.environ.get(
        "DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".devdata")
    )
    MAX_LOGO_BYTES: int = int(os.environ.get("MAX_LOGO_BYTES", "1600000"))
    TRIAL_DAYS: int = int(os.environ.get("TRIAL_DAYS", "14"))

    # --- mail (never crash; record-only when SMTP_HOST empty)
    SMTP_HOST: str = os.environ.get("SMTP_HOST", "")
    SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER: str = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM: str = os.environ.get("MAIL_FROM", "Rowdy's Den <no-reply@localhost>")
    APP_PUBLIC_URL: str = os.environ.get("APP_PUBLIC_URL", "")

    @property
    def is_mock_db(self) -> bool:
        return self.MONGODB_URI.startswith("mongomock://")

    @property
    def google_audiences(self) -> list:
        """All OAuth client IDs a Google ID token may be issued for (web + app)."""
        out = []
        if self.GOOGLE_CLIENT_ID:
            out.append(self.GOOGLE_CLIENT_ID)
        for c in self.GOOGLE_CLIENT_IDS:
            if c not in out:
                out.append(c)
        return out


settings = Settings()
