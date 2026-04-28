from __future__ import annotations

import os

import uvicorn

from app.api import create_app
from app.config import Settings
from app.service import CapacityIntelligenceService
from app.storage import Repository


def build_application(db_path: str | None = None):
    settings = Settings()
    if db_path:
        settings.db_path = db_path
    repository = Repository(settings.db_path)
    service = CapacityIntelligenceService(repository, settings)
    return create_app(service)


app = build_application()


def run() -> None:
    host = os.getenv("CAPACITY_HOST", "127.0.0.1")
    port = int(os.getenv("CAPACITY_PORT", "8000"))
    print(f"Serving Capacity Intelligence MVP on http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
