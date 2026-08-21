"""Tests for the DB-first leaderboard: the `summaries` table, its `fetch_log`
stamp, the `fetch_claims` lease and the single-flight refresh in
`app.routers.stocks._get_summaries_batch`.

Unlike `tests/test_stocks_router.py` (which mocks `fetch_summaries` at the
router's call site), these tests drive the REAL `app.alpaca_client` over an
`httpx.MockTransport` that counts every Alpaca request, so "exactly one
snapshots call" is asserted at the HTTP boundary. `app.storage` is never
mocked: every test gets its own tmp-path SQLite DB. Router module state
(`_refresh_locks`, `_backoff`) is reset per test; a "process restart" is
simulated by resetting it again mid-test while keeping the DB file.

Clocks: `fake_utcnow` drives TTL / lease arithmetic (`stocks._utcnow`),
`fake_clock` drives backoff windows (`stocks._monotonic`). Nothing sleeps
except the deliberate `time.sleep` inside the slow transport used to hold
a leader "in flight" while a burst of waiters queues behind it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.backfill as backfill
import app.routers.stocks as stocks_router
from app import alpaca_client, config, storage
from app.alpaca_client import AlpacaError
from app.config import CACHE_TTL_SECONDS, FETCH_LEASE_SECONDS
from app.main import app
from app.storage import SUMMARIES_KEY, SUMMARIES_TIER, SummaryRow
from app.tickers import TICKER_SYMBOLS

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _FakeWallClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class _FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Alpaca:
    """Counting `httpx.MockTransport` standing in for the Alpaca data API.

    `mode` selects the snapshots answer: ``"ok"`` (every symbol priced,
    ``price = base + index``), ``"429"`` (rate limited -> all-error
    batch), ``"500"``. `delay` holds each request for that many seconds
    (served in a worker thread, so the event loop keeps running and a
    burst of waiters can pile up behind the leader).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.snapshot_calls = 0
        self.mode = "ok"
        self.delay = 0.0
        self.base = 100.0
        self._lock = threading.Lock()
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.calls += 1
            if request.url.path == "/v2/stocks/snapshots":
                self.snapshot_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if request.url.path != "/v2/stocks/snapshots":
            return httpx.Response(404)
        if self.mode == "429":
            return httpx.Response(429, text="too many requests")
        if self.mode == "500":
            return httpx.Response(500)
        symbols = request.url.params["symbols"].split(",")
        body = {
            symbol: {
                "latestTrade": {"p": self.base + i},
                "prevDailyBar": {"c": 100.0},
                "minuteBar": {
                    "t": _iso(NOW - timedelta(minutes=1)),
                    "o": self.base + i,
                    "h": self.base + i,
                    "l": self.base + i,
                    "c": self.base + i,
                    "v": 10,
                    "vw": self.base + i,
                    "n": 1,
                },
            }
            for i, symbol in enumerate(symbols)
        }
        return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "stocks.db"))
    monkeypatch.setattr(config, "ALPACA_KEY_ID", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(config, "ALPACA_DATA_BASE_URL", "https://data.alpaca.test")
    _fresh_process()
    storage.init_db()
    yield


def _fresh_process() -> None:
    """Drop every piece of in-memory router state (what a restart loses)."""
    stocks_router._refresh_locks = {}
    stocks_router._backoff = stocks_router._BackoffState()


@pytest.fixture()
def alpaca(monkeypatch) -> _Alpaca:
    fake = _Alpaca()
    monkeypatch.setattr(alpaca_client, "_transport", fake.transport)
    return fake


@pytest.fixture()
def fake_utcnow(monkeypatch) -> _FakeWallClock:
    clock = _FakeWallClock()
    monkeypatch.setattr(stocks_router, "_utcnow", clock)
    return clock


@pytest.fixture()
def fake_clock(monkeypatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(stocks_router, "_monotonic", clock)
    return clock


@pytest.fixture()
def client():
    return TestClient(app)


def _seed_table(fetched_at: str, base: float = 50.0) -> None:
    """Write a full good batch (``price = base + index``, prev close 40)."""
    rows = [
        SummaryRow(ticker=t, price=base + i, previous_close=40.0, currency="USD", error=None)
        for i, t in enumerate(TICKER_SYMBOLS)
    ]
    storage.upsert_summaries(rows, fetched_at)


def _stamp() -> str | None:
    return storage.last_fetch_at(SUMMARIES_TIER, SUMMARIES_KEY)


def _row_count(table: str) -> int:
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _claim_rows() -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute(
            "SELECT tier, ticker, claimed_at FROM fetch_claims ORDER BY tier, ticker"
        ).fetchall()
    finally:
        conn.close()


def _claim_ids() -> dict[tuple[str, str], str]:
    """``{(tier, ticker): claim_id}`` for every live claim row."""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = conn.execute("SELECT tier, ticker, claim_id FROM fetch_claims").fetchall()
    finally:
        conn.close()
    return {(tier, ticker): claim_id for tier, ticker, claim_id in rows}


async def _burst(n: int, path: str = "/api/stocks") -> list[httpx.Response]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return list(await asyncio.gather(*(ac.get(path) for _ in range(n))))


class TestFreshness:
    def test_fresh_table_within_ttl_serves_with_zero_alpaca_calls(
        self, alpaca, client, fake_utcnow
    ):
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS - 1)), base=50.0)

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.calls == 0
        body = response.json()
        assert len(body["stocks"]) == 20
        by_ticker = {s["ticker"]: s for s in body["stocks"]}
        aapl = by_ticker["AAPL"]
        assert aapl["price"] == pytest.approx(50.0)
        assert aapl["previous_close"] == pytest.approx(40.0)
        # change / change_percent are derived on read, not stored.
        assert aapl["change"] == pytest.approx(10.0)
        assert aapl["change_percent"] == pytest.approx(25.0)
        assert aapl["is_stale"] is False
        assert aapl["error"] is None
        assert aapl["name"] == "Apple" and aapl["sector"] == "Technology"

    def test_single_ticker_endpoint_reads_the_same_table(
        self, alpaca, client, fake_utcnow
    ):
        _seed_table(_iso(NOW), base=50.0)

        response = client.get("/api/stocks/MSFT")

        assert response.status_code == 200
        assert response.json()["price"] == pytest.approx(51.0)
        assert alpaca.calls == 0
        assert client.get("/api/stocks/FAKE").status_code == 404

    def test_stale_table_refetches_once_upserts_20_rows_and_stamps(
        self, alpaca, client, fake_utcnow
    ):
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)), base=50.0)
        alpaca.base = 200.0

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.snapshot_calls == 1
        assert _row_count("summaries") == 20
        assert _stamp() == _iso(NOW)
        stored = storage.get_summaries()
        assert stored["AAPL"].price == pytest.approx(200.0)
        assert stored["AAPL"].previous_close == pytest.approx(100.0)
        assert all(row.fetched_at == _iso(NOW) for row in stored.values())
        body = response.json()
        assert all(s["is_stale"] is False for s in body["stocks"])
        assert {s["ticker"] for s in body["stocks"]} == set(TICKER_SYMBOLS)
        # Second poll inside the TTL: served from the table, no call.
        fake_utcnow.advance(CACHE_TTL_SECONDS / 2)
        assert client.get("/api/stocks").status_code == 200
        assert alpaca.snapshot_calls == 1

    def test_table_never_exceeds_20_rows_after_many_refreshes(
        self, alpaca, client, fake_utcnow
    ):
        for i in range(6):
            fake_utcnow.advance(CACHE_TTL_SECONDS + 1)
            alpaca.base = 100.0 + i
            assert client.get("/api/stocks").status_code == 200
            assert _row_count("summaries") == 20

        assert alpaca.snapshot_calls == 6
        assert storage.get_summaries()["AAPL"].price == pytest.approx(105.0)
        # Exactly one fetch_log stamp for the leaderboard, too.
        conn = sqlite3.connect(config.DB_PATH)
        try:
            stamps = conn.execute(
                "SELECT COUNT(*) FROM fetch_log WHERE tier = ?", (SUMMARIES_TIER,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert stamps == 1

    def test_cold_start_fetches_and_persists_minute_bars(
        self, alpaca, client, fake_utcnow
    ):
        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.snapshot_calls == 1
        assert _row_count("summaries") == 20
        assert _row_count("bars_minute") == 20
        # A successful fetch leaves no lease behind.
        assert _claim_rows() == []


class TestSingleFlight:
    async def test_concurrent_burst_on_stale_table_makes_one_alpaca_call(
        self, alpaca, fake_utcnow
    ):
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)))
        alpaca.delay = 0.2

        responses = await _burst(10)

        assert alpaca.snapshot_calls == 1
        assert all(r.status_code == 200 for r in responses)
        for r in responses:
            stocks = r.json()["stocks"]
            assert len(stocks) == 20
            assert all(s["is_stale"] is False for s in stocks)
            assert all(s["price"] == pytest.approx(100.0 + i) for i, s in enumerate(stocks))
        assert _row_count("summaries") == 20

    async def test_concurrent_burst_on_empty_table_makes_one_alpaca_call(
        self, alpaca, fake_utcnow
    ):
        alpaca.delay = 0.2

        responses = await _burst(10)

        assert alpaca.snapshot_calls == 1
        assert all(r.status_code == 200 for r in responses)
        assert all(
            s["is_stale"] is False for r in responses for s in r.json()["stocks"]
        )

    async def test_leader_total_failure_waiters_do_not_retry(
        self, alpaca, fake_utcnow
    ):
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)), base=50.0)
        alpaca.mode = "429"
        alpaca.delay = 0.2

        responses = await _burst(10)

        # Exactly one attempt for the whole burst, ONE recorded failure
        # (not ten): waiters re-check backoff after the lock and serve
        # stale instead of each retrying.
        assert alpaca.snapshot_calls == 1
        assert stocks_router._backoff.consecutive_failures == 1
        assert all(r.status_code == 200 for r in responses)
        for r in responses:
            stocks = r.json()["stocks"]
            assert len(stocks) == 20
            by_ticker = {s["ticker"]: s for s in stocks}
            assert all(s["is_stale"] is True for s in stocks)
            assert all(s["error"] is None for s in stocks)
            # Previous table values survive -- price AND previous_close/change.
            assert by_ticker["AAPL"]["price"] == pytest.approx(50.0)
            assert by_ticker["AAPL"]["previous_close"] == pytest.approx(40.0)
            assert by_ticker["AAPL"]["change"] == pytest.approx(10.0)
            assert by_ticker["NFLX"]["price"] == pytest.approx(69.0)
        # Nothing was written over the good rows or the stamp.
        assert storage.get_summaries()["AAPL"].price == pytest.approx(50.0)
        assert _stamp() == _iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1))
        # The lease was released despite the failure.
        assert _claim_rows() == []

    def test_requests_during_backoff_serve_table_stale_without_alpaca(
        self, alpaca, client, fake_utcnow, fake_clock
    ):
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)))
        alpaca.mode = "429"
        assert client.get("/api/stocks").status_code == 200
        assert alpaca.snapshot_calls == 1

        for _ in range(3):
            fake_clock.advance(20.0)
            fake_utcnow.advance(20.0)
            response = client.get("/api/stocks")
            assert response.status_code == 200
            assert all(s["is_stale"] is True for s in response.json()["stocks"])
        assert alpaca.snapshot_calls == 1

        # Backoff lifts -> retry; success rewrites the table and resets.
        fake_clock.advance(config.BACKOFF_BASE_SECONDS + 1)
        alpaca.mode = "ok"
        response = client.get("/api/stocks")
        assert alpaca.snapshot_calls == 2
        assert all(s["is_stale"] is False for s in response.json()["stocks"])
        assert stocks_router._backoff.consecutive_failures == 0

    def test_partial_failure_is_stored_per_row_and_served_per_row(
        self, alpaca, client, fake_utcnow
    ):
        good = alpaca._handle  # keep the default handler for the rest

        def drop_nflx(request: httpx.Request) -> httpx.Response:
            response = good(request)
            if response.status_code != 200:
                return response
            body = response.json()
            body.pop("NFLX", None)
            return httpx.Response(200, json=body)

        alpaca.transport = httpx.MockTransport(drop_nflx)
        alpaca_client._transport = alpaca.transport

        response = client.get("/api/stocks")

        assert response.status_code == 200
        by_ticker = {s["ticker"]: s for s in response.json()["stocks"]}
        assert by_ticker["NFLX"]["is_stale"] is True
        assert by_ticker["NFLX"]["error"] is not None
        assert by_ticker["AAPL"]["is_stale"] is False
        stored = storage.get_summaries()
        assert stored["NFLX"].error is not None and stored["NFLX"].price is None
        assert stored["AAPL"].error is None
        assert stocks_router._backoff.consecutive_failures == 0
        # A fresh read reproduces the per-row flags from the table.
        again = client.get("/api/stocks").json()
        assert alpaca.calls == 1
        flags = {s["ticker"]: s["is_stale"] for s in again["stocks"]}
        assert flags["NFLX"] is True and flags["AAPL"] is False


class TestLateWriter:
    def test_older_batch_leaves_rows_and_stamp_unchanged(self):
        _seed_table(_iso(NOW), base=50.0)

        _seed_table(_iso(NOW - timedelta(seconds=5)), base=999.0)

        stored = storage.get_summaries()
        assert stored["AAPL"].price == pytest.approx(50.0)
        assert all(row.fetched_at == _iso(NOW) for row in stored.values())
        assert _stamp() == _iso(NOW)
        assert _row_count("summaries") == 20

    def test_same_fetched_at_is_a_no_op(self):
        _seed_table(_iso(NOW), base=50.0)

        _seed_table(_iso(NOW), base=999.0)

        assert storage.get_summaries()["AAPL"].price == pytest.approx(50.0)
        assert _stamp() == _iso(NOW)

    def test_newer_batch_replaces_rows_and_stamp(self):
        _seed_table(_iso(NOW), base=50.0)

        _seed_table(_iso(NOW + timedelta(seconds=1)), base=999.0)

        stored = storage.get_summaries()
        assert stored["AAPL"].price == pytest.approx(999.0)
        assert _stamp() == _iso(NOW + timedelta(seconds=1))
        assert _row_count("summaries") == 20

    def test_rows_and_stamp_are_written_atomically(self, monkeypatch):
        # A failure mid-transaction leaves neither rows nor stamp behind.
        rows = [
            SummaryRow(ticker=t, price=1.0, previous_close=1.0, currency="USD", error=None)
            for t in TICKER_SYMBOLS
        ]
        rows.append(SummaryRow(ticker="X", price=1.0, previous_close=1.0, currency=None, error=None))  # type: ignore[arg-type]

        with pytest.raises(sqlite3.IntegrityError):
            storage.upsert_summaries(rows, _iso(NOW))

        assert storage.get_summaries() == {}
        assert _stamp() is None


_HEX = "0123456789abcdef"


def _is_claim_id(value: object) -> bool:
    """A `try_claim` fencing token: 32 lowercase hex chars (``uuid4().hex``)."""
    return isinstance(value, str) and len(value) == 32 and all(c in _HEX for c in value)


class TestLease:
    def test_try_claim_once_then_blocked_until_expiry(self):
        now = _iso(NOW)
        first = storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, now, FETCH_LEASE_SECONDS)
        assert _is_claim_id(first)
        assert (
            storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, now, FETCH_LEASE_SECONDS)
            is None
        )
        inside = _iso(NOW + timedelta(seconds=FETCH_LEASE_SECONDS - 1))
        assert (
            storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, inside, FETCH_LEASE_SECONDS)
            is None
        )
        expired = _iso(NOW + timedelta(seconds=FETCH_LEASE_SECONDS + 1))
        second = storage.try_claim(
            SUMMARIES_TIER, SUMMARIES_KEY, expired, FETCH_LEASE_SECONDS
        )
        # A takeover mints a NEW fencing token ...
        assert _is_claim_id(second)
        assert second != first
        # ... and refreshes the stamp: it is held again from `expired`.
        assert _claim_rows() == [(SUMMARIES_TIER, SUMMARIES_KEY, expired)]
        assert _claim_ids() == {(SUMMARIES_TIER, SUMMARIES_KEY): second}
        assert (
            storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, expired, FETCH_LEASE_SECONDS)
            is None
        )

    def test_release_claim_with_its_id_frees_it_immediately(self):
        now = _iso(NOW)
        claim_id = storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, now, FETCH_LEASE_SECONDS)
        assert storage.release_claim(SUMMARIES_TIER, SUMMARIES_KEY, claim_id) is True
        assert _claim_rows() == []
        assert _is_claim_id(
            storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, now, FETCH_LEASE_SECONDS)
        )
        # Releasing something never claimed is harmless and reports no-op.
        assert storage.release_claim("minute", "AAPL", "0" * 32) is False
        # A second release with an already-used id is a no-op too.
        assert storage.release_claim(SUMMARIES_TIER, SUMMARIES_KEY, claim_id) is False

    def test_release_claim_with_wrong_id_is_a_no_op(self):
        now = _iso(NOW)
        claim_id = storage.try_claim("minute", "AAPL", now, FETCH_LEASE_SECONDS)
        assert _is_claim_id(claim_id)

        assert storage.release_claim("minute", "AAPL", "not-our-claim") is False
        assert storage.release_claim("minute", "AAPL", "f" * 32) is False

        # The row is still there, still ours, still blocking others.
        assert _claim_rows() == [("minute", "AAPL", now)]
        assert _claim_ids() == {("minute", "AAPL"): claim_id}
        assert storage.try_claim("minute", "AAPL", now, FETCH_LEASE_SECONDS) is None
        # The right id still works afterwards.
        assert storage.release_claim("minute", "AAPL", claim_id) is True
        assert _claim_rows() == []

    def test_superseded_holder_cannot_release_the_new_claim(self):
        # Worker A claims, then stalls past its lease ...
        a_id = storage.try_claim("hour", "AAPL", _iso(NOW), FETCH_LEASE_SECONDS)
        assert _is_claim_id(a_id)
        # ... so worker B takes the claim over with a fresh token.
        later = _iso(NOW + timedelta(seconds=FETCH_LEASE_SECONDS + 1))
        b_id = storage.try_claim("hour", "AAPL", later, FETCH_LEASE_SECONDS)
        assert _is_claim_id(b_id) and b_id != a_id

        # A finally finishes and releases with ITS id: B's claim must stay.
        assert storage.release_claim("hour", "AAPL", a_id) is False
        assert _claim_rows() == [("hour", "AAPL", later)]
        assert _claim_ids() == {("hour", "AAPL"): b_id}
        # A third worker still cannot get in while B's lease is live.
        assert storage.try_claim("hour", "AAPL", later, FETCH_LEASE_SECONDS) is None

        # B's own release, with B's id, does remove it.
        assert storage.release_claim("hour", "AAPL", b_id) is True
        assert _claim_rows() == []

    def test_claims_are_per_pair(self):
        now = _iso(NOW)
        ids = {
            storage.try_claim("minute", "AAPL", now, FETCH_LEASE_SECONDS),
            storage.try_claim("minute", "MSFT", now, FETCH_LEASE_SECONDS),
            storage.try_claim("hour", "AAPL", now, FETCH_LEASE_SECONDS),
        }
        assert all(_is_claim_id(i) for i in ids)
        assert len(ids) == 3  # every claim gets its own token
        assert storage.try_claim("minute", "AAPL", now, FETCH_LEASE_SECONDS) is None

    def test_rejects_unknown_tier(self):
        with pytest.raises(ValueError):
            storage.try_claim("weekly", "AAPL", _iso(NOW), 10)

    def test_failed_fetch_releases_the_claim(self, alpaca, client, fake_utcnow):
        alpaca.mode = "500"

        assert client.get("/api/stocks").status_code == 200

        assert alpaca.snapshot_calls == 1
        assert _claim_rows() == []
        assert stocks_router._backoff.consecutive_failures == 1

    def test_unexpected_error_releases_claim_and_propagates(
        self, alpaca, fake_utcnow
    ):
        with patch(
            "app.routers.stocks.alpaca_client.fetch_summaries",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(stocks_router._get_summaries_batch())

        assert _claim_rows() == []
        # The lock was released too: a later call proceeds normally.
        assert asyncio.run(stocks_router._get_summaries_batch())["AAPL"].price == 100.0
        assert alpaca.snapshot_calls == 1

    def test_claim_held_by_another_process_serves_table_without_alpaca(
        self, alpaca, client, fake_utcnow
    ):
        # "Another process" took the lease a moment ago (seeded directly)
        # and is fetching into the shared table right now.
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)), base=50.0)
        assert storage.try_claim(
            SUMMARIES_TIER, SUMMARIES_KEY, _iso(NOW - timedelta(seconds=1)), FETCH_LEASE_SECONDS
        )

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.calls == 0
        stocks = response.json()["stocks"]
        assert all(s["is_stale"] is True for s in stocks)
        assert {s["ticker"]: s["price"] for s in stocks}["AAPL"] == pytest.approx(50.0)
        # The other process's lease is intact (we did not steal or release it).
        assert _claim_rows() == [
            (SUMMARIES_TIER, SUMMARIES_KEY, _iso(NOW - timedelta(seconds=1)))
        ]
        assert stocks_router._backoff.consecutive_failures == 0

        # Once the lease expires (holder crashed) we take over and fetch.
        fake_utcnow.advance(FETCH_LEASE_SECONDS + 1)
        assert client.get("/api/stocks").status_code == 200
        assert alpaca.snapshot_calls == 1

    def test_claim_held_by_another_process_with_empty_table_is_200(
        self, alpaca, client, fake_utcnow
    ):
        assert storage.try_claim(SUMMARIES_TIER, SUMMARIES_KEY, _iso(NOW), FETCH_LEASE_SECONDS)

        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.calls == 0
        stocks = response.json()["stocks"]
        assert len(stocks) == 20
        assert all(s["error"] == stocks_router.NO_DATA_ERROR for s in stocks)
        assert all(s["is_stale"] is True for s in stocks)

    def test_superseded_summaries_fetch_leaves_the_new_claim_in_place(
        self, alpaca, fake_utcnow
    ):
        # Our request takes the lease, then its Alpaca call stalls past the
        # lease window; meanwhile "another process" takes the claim over.
        real_fetch = alpaca_client.fetch_summaries
        taken_over: dict[str, str] = {}

        def stalled_fetch(symbols):
            fake_utcnow.advance(FETCH_LEASE_SECONDS + 1)
            other = storage.try_claim(
                SUMMARIES_TIER, SUMMARIES_KEY, _iso(fake_utcnow.now), FETCH_LEASE_SECONDS
            )
            assert _is_claim_id(other)
            taken_over["id"] = other
            return real_fetch(symbols)

        with patch(
            "app.routers.stocks.alpaca_client.fetch_summaries", side_effect=stalled_fetch
        ):
            batch = asyncio.run(stocks_router._get_summaries_batch())

        # Our request completed normally ...
        assert batch["AAPL"].price == 100.0
        assert alpaca.snapshot_calls == 1
        # ... but its release did NOT delete the second claimant's row.
        assert _claim_ids() == {(SUMMARIES_TIER, SUMMARIES_KEY): taken_over["id"]}
        assert _claim_rows() == [(SUMMARIES_TIER, SUMMARIES_KEY, _iso(fake_utcnow.now))]
        # The second claimant's own release is what clears it.
        assert (
            storage.release_claim(SUMMARIES_TIER, SUMMARIES_KEY, taken_over["id"]) is True
        )
        assert _claim_rows() == []

    def test_superseded_summaries_fetch_that_fails_leaves_the_new_claim_in_place(
        self, alpaca, fake_utcnow
    ):
        taken_over: dict[str, str] = {}

        def stalled_then_boom(symbols):
            fake_utcnow.advance(FETCH_LEASE_SECONDS + 1)
            taken_over["id"] = storage.try_claim(
                SUMMARIES_TIER, SUMMARIES_KEY, _iso(fake_utcnow.now), FETCH_LEASE_SECONDS
            )
            raise RuntimeError("boom")

        with patch(
            "app.routers.stocks.alpaca_client.fetch_summaries", side_effect=stalled_then_boom
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(stocks_router._get_summaries_batch())

        assert _is_claim_id(taken_over["id"])
        assert _claim_ids() == {(SUMMARIES_TIER, SUMMARIES_KEY): taken_over["id"]}


class TestRestart:
    def test_new_process_within_ttl_serves_table_without_alpaca(
        self, alpaca, client, fake_utcnow
    ):
        assert client.get("/api/stocks").status_code == 200
        assert alpaca.snapshot_calls == 1

        _fresh_process()  # same DB file, no in-memory state
        fake_utcnow.advance(CACHE_TTL_SECONDS - 1)
        response = client.get("/api/stocks")

        assert response.status_code == 200
        assert alpaca.snapshot_calls == 1
        assert all(s["is_stale"] is False for s in response.json()["stocks"])

    def test_new_process_after_ttl_refetches_once(self, alpaca, client, fake_utcnow):
        assert client.get("/api/stocks").status_code == 200
        assert alpaca.snapshot_calls == 1

        _fresh_process()
        fake_utcnow.advance(CACHE_TTL_SECONDS + 1)
        alpaca.base = 300.0
        first = client.get("/api/stocks")
        second = client.get("/api/stocks")

        assert alpaca.snapshot_calls == 2
        assert first.json()["stocks"][0]["price"] == pytest.approx(300.0)
        assert second.json()["stocks"][0]["price"] == pytest.approx(300.0)
        assert _row_count("summaries") == 20

    def test_backfill_sweep_repopulates_leaderboard_before_any_poll(
        self, alpaca, fake_utcnow
    ):
        # Restart scenario: stale table, nobody polling. The sweep's first
        # step refreshes the leaderboard (one snapshots call); a later
        # sweep inside the TTL costs nothing.
        _seed_table(_iso(NOW - timedelta(seconds=CACHE_TTL_SECONDS + 1)), base=50.0)
        with patch("app.routers.stocks.alpaca_client.fetch_bars", return_value=[]):
            asyncio.run(backfill.sweep())
            assert alpaca.snapshot_calls == 1
            assert storage.get_summaries()["AAPL"].price == pytest.approx(100.0)
            assert _stamp() == _iso(NOW)

            asyncio.run(backfill.sweep())
        assert alpaca.snapshot_calls == 1


class TestColdStartAlpacaDown:
    def test_empty_table_and_alpaca_down_is_200_error_batch_with_one_backoff(
        self, alpaca, client, fake_utcnow, fake_clock
    ):
        alpaca.mode = "429"

        first = client.get("/api/stocks")

        assert first.status_code == 200
        stocks = first.json()["stocks"]
        assert len(stocks) == 20
        assert all(s["is_stale"] is True for s in stocks)
        assert all(s["error"] is not None and s["price"] is None for s in stocks)
        assert alpaca.snapshot_calls == 1
        assert stocks_router._backoff.consecutive_failures == 1
        assert _row_count("summaries") == 0
        assert _stamp() is None

        # Polls inside the backoff window: still 200, still no Alpaca.
        fake_clock.advance(1.0)
        second = client.get("/api/stocks/AAPL")
        assert second.status_code == 200
        assert second.json()["error"] is not None
        assert alpaca.snapshot_calls == 1
        assert stocks_router._backoff.consecutive_failures == 1


class TestAlpacaClientTimeout:
    def test_client_uses_configured_timeout(self, monkeypatch):
        monkeypatch.setattr(config, "ALPACA_TIMEOUT_SECONDS", 3.5)

        with alpaca_client._client() as client:
            assert client.timeout == httpx.Timeout(3.5)

    def test_default_timeout_and_lease(self):
        assert config.ALPACA_TIMEOUT_SECONDS == 5.0
        assert config.FETCH_LEASE_SECONDS == 10.0
        assert config.FETCH_LEASE_SECONDS >= config.ALPACA_TIMEOUT_SECONDS


class TestHistoryLease:
    async def test_refresh_history_honours_and_clears_the_lease(self, fake_utcnow):
        # Lease held by "another process": no fetch, returns False.
        other_id = storage.try_claim("minute", "AAPL", _iso(NOW), FETCH_LEASE_SECONDS)
        assert _is_claim_id(other_id)
        with patch("app.routers.stocks.alpaca_client.fetch_bars", return_value=[]) as mock_fetch:
            assert await stocks_router.refresh_history("AAPL", "minute") is False
            assert mock_fetch.call_count == 0
            assert storage.last_fetch_at("minute", "AAPL") is None

            # Lease released (holder finished): we fetch, and leave no lease.
            assert storage.release_claim("minute", "AAPL", other_id) is True
            assert await stocks_router.refresh_history("AAPL", "minute") is True
            assert mock_fetch.call_count == 1
            assert storage.last_fetch_at("minute", "AAPL") == _iso(NOW)
            assert _claim_rows() == []

    async def test_superseded_history_refresh_leaves_the_new_claim_in_place(
        self, fake_utcnow
    ):
        taken_over: dict[str, str] = {}

        def stalled_fetch_bars(ticker, timeframe, start):
            # We overran the lease; another process took the pair over.
            fake_utcnow.advance(FETCH_LEASE_SECONDS + 1)
            taken_over["id"] = storage.try_claim(
                "minute", "AAPL", _iso(fake_utcnow.now), FETCH_LEASE_SECONDS
            )
            return []

        with patch(
            "app.routers.stocks.alpaca_client.fetch_bars", side_effect=stalled_fetch_bars
        ):
            assert await stocks_router.refresh_history("AAPL", "minute") is True

        assert _is_claim_id(taken_over["id"])
        # Our `finally` release was fenced out: the takeover's claim survives.
        assert _claim_ids() == {("minute", "AAPL"): taken_over["id"]}
        assert _claim_rows() == [("minute", "AAPL", _iso(fake_utcnow.now))]
        assert storage.release_claim("minute", "AAPL", taken_over["id"]) is True
        assert _claim_rows() == []

    async def test_refresh_history_releases_lease_on_alpaca_error(self, fake_utcnow):
        with patch(
            "app.routers.stocks.alpaca_client.fetch_bars",
            side_effect=AlpacaError("alpaca error: down"),
        ):
            with pytest.raises(AlpacaError):
                await stocks_router.refresh_history("AAPL", "hour")
        assert _claim_rows() == []
