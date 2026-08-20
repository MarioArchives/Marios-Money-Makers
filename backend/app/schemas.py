from pydantic import BaseModel


class StockSummary(BaseModel):
    ticker: str
    name: str
    sector: str
    price: float | None
    currency: str = "GBP"
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
