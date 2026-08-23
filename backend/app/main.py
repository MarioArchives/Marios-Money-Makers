"""FastAPI application entrypoint: app construction and middleware wiring."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import backfill, config, storage
from app.config import ALLOWED_ORIGINS
from app.routers.market import router as market_router
from app.routers.stocks import router as stocks_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the SQLite tables, then run the backfill sweep until shutdown.

    ``config.BACKFILL_ENABLED`` is read at call time so tests can monkeypatch it.
    """
    storage.init_db()
    storage.ensure_bars_adjustment(config.ALPACA_BARS_ADJUSTMENT)
    task: asyncio.Task[None] | None = None
    if config.BACKFILL_ENABLED:
        task = asyncio.create_task(backfill.run_forever(), name="backfill")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Mario's Money Makers API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(stocks_router)
app.include_router(market_router)


@app.get("/api/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness probe for Docker healthchecks / deploy scripts (no I/O)."""
    return {"status": "ok"}
