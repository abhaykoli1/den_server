import os

import uvicorn  # noqa: F401  (loads .env via config import below)

from app.config import settings  # noqa: E402  (loads backend/.env as a side effect)

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
