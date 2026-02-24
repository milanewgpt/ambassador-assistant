"""
Ambassador Assistant — FastAPI application entry point.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import get_pool, close_pool
from app.routers import health, ingest
from app.services.scraper import shutdown_scraper
from app.services.telegram_bot import build_bot_app
from app.utils.logging import log

_bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app

    log.info("Starting Ambassador Assistant API …")
    await get_pool()

    # Start Telegram bot polling in background
    if settings.TELEGRAM_BOT_TOKEN:
        try:
            _bot_app = build_bot_app()
            await _bot_app.initialize()
            await _bot_app.start()
            await _bot_app.updater.start_polling(drop_pending_updates=True)
            log.info("Telegram bot polling started.")
        except Exception as exc:
            log.error("Failed to start Telegram bot: %s", exc)
            _bot_app = None
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled.")

    yield

    log.info("Shutting down …")
    if _bot_app:
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()
    await shutdown_scraper()
    await close_pool()


app = FastAPI(
    title="Ambassador Assistant",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(ingest.router)
