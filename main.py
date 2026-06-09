"""
main.py
────────
Application entry point.

Startup sequence
────────────────
  1. Initialise the SQLite database.
  2. Start the APScheduler background job for proactive follow-ups.
  3. Mount all API routes.
  4. Start uvicorn.
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.agent.memory import init_db
from app.api.endpoints import router
from app.core.config import settings
from app.core.logger import get_logger
from app.services.scheduler import start_scheduler, stop_scheduler

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    logger.info("Starting up...")
    await init_db()
    start_scheduler()
    yield
    # ── Shutdown ─────────────────────────────────────────────
    logger.info("Shutting down...")
    stop_scheduler()


app = FastAPI(
    title="Human-Sounding WhatsApp Agent",
    description="A WhatsApp AI agent that feels like texting a real person.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )
