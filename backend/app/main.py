"""FastAPI application entrypoint.

Creates the `FastAPI` app, configures CORS from
`app.config.ALLOWED_ORIGINS` (GET-only, per the plan's error-handling
section), and wires up the stocks router and the market router (the
Alpaca market clock, `GET /api/market/clock`). The lifespan hook initialises
the SQLite store (and invalidates stale bar fetch stamps if
``ALPACA_BARS_ADJUSTMENT`` changed since the DB was last used) and, unless `app.config.BACKFILL_ENABLED` is off, runs
the :mod:`app.backfill` sweep as a background task for the life of the
process (cancelled on shutdown). This module is plumbing (app
construction + middleware configuration), not business logic.
"""

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

    ``config.BACKFILL_ENABLED`` is read at call time (module attribute,
    not imported) so tests can monkeypatch it off and use ``TestClient``
    lifespans without starting a real sweep.
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
