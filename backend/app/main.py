"""FastAPI application entrypoint.

Creates the `FastAPI` app, configures CORS from
`app.config.ALLOWED_ORIGINS` (GET-only, per the plan's error-handling
section), and wires up the stocks router. This module is plumbing (app
construction + middleware configuration), not business logic, so unlike
`routers/stocks.py` it is fully implemented rather than a skeleton.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import ALLOWED_ORIGINS
from app.routers.stocks import router as stocks_router

app = FastAPI(title="UK Stocks Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(stocks_router)
