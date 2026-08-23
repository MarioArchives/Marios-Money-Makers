"""Tests for `GET /api/market/clock` (`app/routers/market.py`).

Same conventions as `tests/test_stocks_router.py`: FastAPI's `TestClient`
against the real app, a tmp-path SQLite DB via a monkeypatched
`app.config.DB_PATH`, `alpaca_client.fetch_clock` patched at the reference
the router calls (`app.routers.market.alpaca_client.fetch_clock` -- it runs
in a worker thread, so the module-attribute patch is visible there too),
and a frozen wall clock via `app.routers.market._utcnow`.

Contract under test: the `meta` table (key `market_clock`) IS the cache.
The stored clock is served with zero Alpaca calls until one of the
boundaries it carries arrives -- `now` before both `next_open` and
`next_close`. That is the ONLY expiry rule: there is no TTL, so an old
`fetched_at` is not by itself a reason to refetch. When a boundary has
passed, one worker (the in-process `asyncio.Lock` -- there is no
cross-process lease any more) refetches and rewrites the key. On Alpaca failure the
stored clock is served `is_stale=True` with the error; 503 only when
nothing has ever been stored.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.routers.market as market_router
from app import config, storage
from app.alpaca_client import RATE_LIMIT_ERROR_PREFIX, AlpacaError, MarketClock
from app.main import app

# Thursday 20 Aug 2026 12:00Z = 08:00 ET: a weekday, before the open.
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
OPEN_AT = NOW + timedelta(hours=1, minutes=30)  # 13:30Z
CLOSE_AT = NOW + timedelta(hours=8)  # 20:00Z


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


CLOSED_CLOCK = MarketClock(
    timestamp=_iso(NOW),
    is_open=False,
    next_open=_iso(OPEN_AT),
    next_close=_iso(CLOSE_AT),
)
OPEN_CLOCK = MarketClock(
    timestamp=_iso(NOW + timedelta(hours=2)),
    is_open=True,
    next_open=_iso(OPEN_AT + timedelta(days=1)),
    next_close=_iso(CLOSE_AT),
)


# A stored clock whose `next_open` has already arrived: with no TTL, a
# passed boundary is the only thing that makes a stored clock stale.
PASSED_CLOCK = MarketClock(
    timestamp=_iso(NOW - timedelta(hours=2)),
    is_open=False,
    next_open=_iso(NOW - timedelta(seconds=5)),
    next_close=_iso(CLOSE_AT),
)


class _FakeWallClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "stocks.db"))
    market_router._refresh_locks = {}
    yield


@pytest.fixture()
def fake_utcnow(monkeypatch):
    clock = _FakeWallClock()
    monkeypatch.setattr(market_router, "_utcnow", clock)
    return clock


@pytest.fixture()
def client():
    return TestClient(app)


def _meta_value() -> dict | None:
    with sqlite3.connect(config.DB_PATH) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (storage.MARKET_CLOCK_META_KEY,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def _table_exists(name: str) -> bool:
    with sqlite3.connect(config.DB_PATH) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ).fetchone()
    return row is not None


def _seed_legacy_claim_table(tier: str, ticker: str, claimed_at: str) -> None:
    """Recreate the dropped `fetch_claims` table with one live claim row for
    the clock pseudo-pair, as an older build would have left it."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fetch_claims ("
            "tier TEXT NOT NULL, ticker TEXT NOT NULL, claimed_at TEXT NOT NULL, "
            "claim_id TEXT NOT NULL, PRIMARY KEY (tier, ticker))"
        )
        conn.execute(
            "INSERT INTO fetch_claims (tier, ticker, claimed_at, claim_id) "
            "VALUES (?, ?, ?, ?)",
            (tier, ticker, claimed_at, "f" * 32),
        )
        conn.commit()


def _expected(clock: MarketClock, *, fetched_at: str, is_stale: bool, error=None) -> dict:
    return {
        "timestamp": clock.timestamp,
        "is_open": clock.is_open,
        "next_open": clock.next_open,
        "next_close": clock.next_close,
        "fetched_at": fetched_at,
        "is_stale": is_stale,
        "error": error,
    }


class TestMarketClockEndpoint:
    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_cold_start_fetches_stores_and_serves_fresh(self, mock_fetch, client, fake_utcnow):
        mock_fetch.return_value = CLOSED_CLOCK

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert response.json() == _expected(CLOSED_CLOCK, fetched_at=_iso(NOW), is_stale=False)
        assert mock_fetch.call_count == 1
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.is_open is False
        assert stored.fetched_at == _iso(NOW)
        meta = _meta_value()
        assert meta is not None
        assert meta["is_open"] is False
        assert meta["next_open"] == _iso(OPEN_AT)

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_fresh_stored_clock_is_served_with_zero_alpaca_calls(
        self, mock_fetch, client, fake_utcnow
    ):
        fetched_at = _iso(NOW - timedelta(seconds=30))
        storage.set_market_clock(CLOSED_CLOCK, fetched_at=fetched_at)

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert response.json() == _expected(CLOSED_CLOCK, fetched_at=fetched_at, is_stale=False)
        assert mock_fetch.call_count == 0

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_an_old_fetch_stamp_alone_is_not_a_reason_to_refetch(
        self, mock_fetch, client, fake_utcnow
    ):
        # No TTL: a clock fetched days ago still describes the present as
        # long as neither boundary it carries has arrived (e.g. one fetched
        # on Friday afternoon, read again on Sunday night).
        ancient = _iso(NOW - timedelta(days=2))
        storage.set_market_clock(CLOSED_CLOCK, fetched_at=ancient)

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert response.json() == _expected(CLOSED_CLOCK, fetched_at=ancient, is_stale=False)
        assert mock_fetch.call_count == 0

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_a_passed_boundary_refetches_and_replaces_the_stored_clock(
        self, mock_fetch, client, fake_utcnow
    ):
        storage.set_market_clock(PASSED_CLOCK, fetched_at=_iso(NOW - timedelta(hours=2)))
        refreshed = MarketClock(
            timestamp=_iso(NOW),
            is_open=False,
            next_open=_iso(OPEN_AT),
            next_close=_iso(CLOSE_AT),
        )
        mock_fetch.return_value = refreshed

        response = client.get("/api/market/clock")

        assert mock_fetch.call_count == 1
        assert response.status_code == 200
        assert response.json() == _expected(refreshed, fetched_at=_iso(NOW), is_stale=False)
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.timestamp == _iso(NOW)
        assert stored.fetched_at == _iso(NOW)

    @pytest.mark.parametrize(
        "stored",
        [
            # Closed, and the open we were counting down to has just passed.
            MarketClock(
                timestamp=_iso(NOW - timedelta(seconds=10)),
                is_open=False,
                next_open=_iso(NOW - timedelta(seconds=5)),
                next_close=_iso(CLOSE_AT),
            ),
            # Open, and the close is exactly now (>= invalidates).
            MarketClock(
                timestamp=_iso(NOW - timedelta(seconds=10)),
                is_open=True,
                next_open=_iso(OPEN_AT + timedelta(days=1)),
                next_close=_iso(NOW),
            ),
        ],
        ids=["next_open_passed", "next_close_reached"],
    )
    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_crossed_boundary_invalidates_the_stored_clock(
        self, mock_fetch, stored, client, fake_utcnow
    ):
        storage.set_market_clock(stored, fetched_at=_iso(NOW - timedelta(seconds=10)))
        mock_fetch.return_value = OPEN_CLOCK

        response = client.get("/api/market/clock")

        assert mock_fetch.call_count == 1
        assert response.status_code == 200
        assert response.json() == _expected(OPEN_CLOCK, fetched_at=_iso(NOW), is_stale=False)

    @pytest.mark.parametrize(
        "error",
        [
            AlpacaError("alpaca error: connection refused"),
            AlpacaError(f"{RATE_LIMIT_ERROR_PREFIX}: HTTP 429", is_rate_limit=True),
        ],
        ids=["plain", "rate_limited"],
    )
    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_alpaca_failure_with_stored_clock_serves_it_stale_and_leaves_meta_alone(
        self, mock_fetch, error, client, fake_utcnow
    ):
        fetched_at = _iso(NOW - timedelta(hours=2))
        storage.set_market_clock(PASSED_CLOCK, fetched_at=fetched_at)
        mock_fetch.side_effect = error

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert response.json() == _expected(
            PASSED_CLOCK, fetched_at=fetched_at, is_stale=True, error=str(error)
        )
        assert mock_fetch.call_count == 1
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.fetched_at == fetched_at

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_alpaca_failure_with_nothing_stored_is_503(self, mock_fetch, client, fake_utcnow):
        mock_fetch.side_effect = AlpacaError("alpaca error: connection refused")

        response = client.get("/api/market/clock")

        assert response.status_code == 503
        assert "alpaca error: connection refused" in response.json()["detail"]
        assert storage.get_market_clock() is None

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_failure_does_not_poison_the_next_request(self, mock_fetch, client, fake_utcnow):
        # No backoff for the clock: the very next request tries Alpaca again.
        mock_fetch.side_effect = [AlpacaError("alpaca error: boom"), CLOSED_CLOCK]

        assert client.get("/api/market/clock").status_code == 503
        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert response.json()["is_stale"] is False
        assert mock_fetch.call_count == 2

    @patch("app.routers.market.alpaca_client.fetch_clock")
    async def test_concurrent_requests_collapse_into_one_fetch(self, mock_fetch, fake_utcnow):
        def slow_fetch(*args, **kwargs):
            time.sleep(0.2)
            return CLOSED_CLOCK

        mock_fetch.side_effect = slow_fetch

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            responses = await asyncio.gather(
                *(async_client.get("/api/market/clock") for _ in range(5))
            )

        assert [r.status_code for r in responses] == [200] * 5
        assert mock_fetch.call_count == 1
        bodies = [r.json() for r in responses]
        assert all(body == bodies[0] for body in bodies)
        assert bodies[0]["is_stale"] is False


class TestRefreshClock:
    """`refresh_clock()` is the sweep's entry point: same refresh, no HTTP,
    never raises for Alpaca reasons."""

    @patch("app.routers.market.alpaca_client.fetch_clock")
    async def test_populates_meta_when_nothing_is_stored(self, mock_fetch, fake_utcnow):
        mock_fetch.return_value = CLOSED_CLOCK

        await market_router.refresh_clock()

        assert mock_fetch.call_count == 1
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.fetched_at == _iso(NOW)

    @patch("app.routers.market.alpaca_client.fetch_clock")
    async def test_noop_when_fresh(self, mock_fetch, fake_utcnow):
        # Old stamp, boundaries still ahead: nothing to do.
        storage.set_market_clock(CLOSED_CLOCK, fetched_at=_iso(NOW - timedelta(days=2)))

        await market_router.refresh_clock()

        assert mock_fetch.call_count == 0

    @patch("app.routers.market.alpaca_client.fetch_clock")
    async def test_refetches_when_stale(self, mock_fetch, fake_utcnow):
        storage.set_market_clock(PASSED_CLOCK, fetched_at=_iso(NOW - timedelta(hours=2)))
        mock_fetch.return_value = OPEN_CLOCK

        await market_router.refresh_clock()

        assert mock_fetch.call_count == 1
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.is_open is True

    @patch("app.routers.market.alpaca_client.fetch_clock")
    async def test_swallows_alpaca_errors(self, mock_fetch, fake_utcnow):
        mock_fetch.side_effect = AlpacaError("alpaca error: boom")

        await market_router.refresh_clock()  # must not raise

        assert storage.get_market_clock() is None


class TestAppWiring:
    def test_market_router_is_mounted_under_api_market(self, client):
        # Via the OpenAPI schema rather than `app.routes`: recent FastAPI
        # keeps included routers as lazy placeholders in `app.routes`.
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/market/clock" in paths
        assert client.get("/api/market/unknown").status_code == 404

    def test_health_is_unchanged(self):
        with TestClient(app) as lifespan_client:
            response = lifespan_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_config_defaults(self):
        # Paper host by default (the README walks users through a paper
        # account); live keys point it at api.alpaca.markets.
        assert config.ALPACA_TRADING_BASE_URL == "https://paper-api.alpaca.markets"
        # The clock expires on its own boundaries, never on a timer: there
        # is no TTL setting to tune (or to leave stale in a deployment).
        assert not hasattr(config, "CLOCK_CACHE_TTL_SECONDS")


class TestNoLeaseTable:
    """The clock refresh used to take a `fetch_claims` lease under the
    pseudo-pair ('clock', '*'). Both are gone: only the in-process lock
    remains, and a legacy claim row must not hold the clock hostage."""

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_a_refresh_creates_no_claim_table(self, mock_fetch, client, fake_utcnow):
        mock_fetch.return_value = CLOSED_CLOCK

        assert client.get("/api/market/clock").status_code == 200

        assert mock_fetch.call_count == 1
        assert not _table_exists("fetch_claims")

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_a_legacy_claim_row_does_not_block_the_clock_refresh(
        self, mock_fetch, client, fake_utcnow
    ):
        # Used to serve the stored clock stale with zero Alpaca calls.
        mock_fetch.return_value = CLOSED_CLOCK
        fetched_at = _iso(NOW - timedelta(hours=2))
        storage.set_market_clock(PASSED_CLOCK, fetched_at=fetched_at)
        _seed_legacy_claim_table("clock", "*", _iso(NOW))

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert mock_fetch.call_count == 1
        assert response.json() == _expected(
            CLOSED_CLOCK, fetched_at=_iso(NOW), is_stale=False
        )
        assert not _table_exists("fetch_claims")

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_a_legacy_claim_row_with_nothing_stored_is_not_a_503(
        self, mock_fetch, client, fake_utcnow
    ):
        # The "lease held elsewhere + nothing stored -> 503" path is gone;
        # 503 now means Alpaca failed with an empty store, nothing else.
        mock_fetch.return_value = CLOSED_CLOCK
        _seed_legacy_claim_table("clock", "*", _iso(NOW))

        response = client.get("/api/market/clock")

        assert response.status_code == 200
        assert mock_fetch.call_count == 1

    @patch("app.routers.market.alpaca_client.fetch_clock")
    def test_unexpected_error_propagates_and_a_later_request_still_refreshes(
        self, mock_fetch, client, fake_utcnow
    ):
        mock_fetch.side_effect = [RuntimeError("boom"), CLOSED_CLOCK]

        with pytest.raises(RuntimeError):
            client.get("/api/market/clock")

        # The lock was released on the exception path.
        response = client.get("/api/market/clock")
        assert response.status_code == 200
        assert response.json()["is_stale"] is False
        assert mock_fetch.call_count == 2
