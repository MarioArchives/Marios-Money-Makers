from typing import Any

from pydantic import BaseModel


class StockSummary(BaseModel):
    ticker: str
    name: str
    sector: str
    price: float | None
    currency: str = "USD"
    previous_close: float | None
    change: float | None
    change_percent: float | None
    is_stale: bool
    error: str | None = None


class StocksResponse(BaseModel):
    updated_at: str
    stocks: list[StockSummary]


class HistoryPoint(BaseModel):
    t: str
    close: float


class HistoryResponse(BaseModel):
    ticker: str
    interval: str
    range: str
    points: list[HistoryPoint]
    is_stale: bool
    error: str | None = None


class StoredBar(BaseModel):
    """One row of a ``bars_<tier>`` table, exactly as stored."""

    ts: str
    price: float
    analytics: dict[str, Any]
    recorded_at: str


class StoredDataResponse(BaseModel):
    """Everything the SQLite store holds for one ticker in one tier."""

    ticker: str
    tier: str
    table: str
    # Stored bars are Alpaca US-equity prices; surfaced so the UI can
    # convert without assuming.
    currency: str = "USD"
    last_fetch_at: str | None
    counts: dict[str, int]
    rows: list[StoredBar]
