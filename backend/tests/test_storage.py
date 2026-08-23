"""Tests for `app.storage`, the three-table SQLite backup store: real
persistence, per-test tmp-path DB (monkeypatched `config.DB_PATH`). Row
shape: ticker, price, analytics(JSON), ts (ISO-8601 UTC), recorded_at."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import config, storage
from app.alpaca_client import Bar, MarketClock

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(ts: str, price: float) -> Bar:
    analytics = json.dumps(
        {"o": price, "h": price, "l": price, "c": price, "v": 500, "vw": price, "n": 7}
    )
    return Bar(ts=ts, price=price, analytics=analytics)


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "stocks.db"))
    yield


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(config.DB_PATH)


def _rows(table: str) -> list[tuple]:
    with _connect() as conn:
        return conn.execute(
            f"SELECT ticker, price, analytics, ts, recorded_at FROM {table} "
            "ORDER BY ticker, ts"
        ).fetchall()


class TestInitDb:
    def test_creates_all_three_tables_and_parent_dirs(self):
        # DB_PATH points inside a not-yet-existing directory; init_db must
        # create it rather than fail.
        storage.init_db()

        with _connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {"bars_minute", "bars_hour", "bars_days"} <= tables

    def test_is_idempotent(self):
        storage.init_db()
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))

        storage.init_db()  # second call must not error or clear data

        assert len(_rows("bars_minute")) == 1


class TestUpsertBars:
    def test_inserts_full_row_shape(self):
        storage.init_db()
        bar = _bar(_iso(NOW - timedelta(minutes=1)), 316.9)

        storage.upsert_bars("minute", "AAPL", [bar], _iso(NOW))

        rows = _rows("bars_minute")
        assert len(rows) == 1
        ticker, price, analytics, ts, recorded_at = rows[0]
        assert ticker == "AAPL"
        assert price == pytest.approx(316.9)
        assert json.loads(analytics) == json.loads(bar.analytics)
        assert ts == bar.ts
        assert recorded_at == _iso(NOW)

    def test_upsert_updates_instead_of_duplicating(self):
        # Re-fetching an overlapping window must leave exactly one row per
        # (ticker, ts), with price/analytics/recorded_at replaced -- the
        # current day's bar keeps changing until the session closes.
        storage.init_db()
        ts = _iso(NOW - timedelta(minutes=1))
        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.0)], _iso(NOW))

        later = _iso(NOW + timedelta(seconds=90))
        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.5)], later)

        rows = _rows("bars_minute")
        assert len(rows) == 1
        assert rows[0][1] == pytest.approx(100.5)
        assert rows[0][4] == later

    def test_same_ts_different_tickers_do_not_collide(self):
        storage.init_db()
        ts = _iso(NOW)
        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.0)], _iso(NOW))
        storage.upsert_bars("minute", "MSFT", [_bar(ts, 484.0)], _iso(NOW))

        assert len(_rows("bars_minute")) == 2

    def test_writes_are_tier_scoped(self):
        storage.init_db()
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))

        assert len(_rows("bars_minute")) == 1
        assert _rows("bars_hour") == []
        assert _rows("bars_days") == []

    def test_creates_tables_defensively_without_prior_init(self):
        # The lifespan hook normally calls init_db, but writes must not
        # depend on it (e.g. first request in a process where the hook
        # did not run).
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))

        assert len(_rows("bars_minute")) == 1


class TestGetBars:
    def test_returns_ts_price_ordered_with_inclusive_since(self):
        storage.init_db()
        t0 = _iso(NOW - timedelta(minutes=3))
        t1 = _iso(NOW - timedelta(minutes=2))
        t2 = _iso(NOW - timedelta(minutes=1))
        # Insert out of order; reads must come back chronological.
        storage.upsert_bars(
            "minute",
            "AAPL",
            [_bar(t2, 102.0), _bar(t0, 100.0), _bar(t1, 101.0)],
            _iso(NOW),
        )

        result = storage.get_bars("minute", "AAPL", since=t1)

        # `since` is inclusive: t1 in, t0 out.
        assert result == [(t1, 101.0), (t2, 102.0)]

    def test_scoped_to_requested_ticker(self):
        storage.init_db()
        ts = _iso(NOW)
        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.0)], _iso(NOW))
        storage.upsert_bars("minute", "MSFT", [_bar(ts, 484.0)], _iso(NOW))

        result = storage.get_bars("minute", "AAPL", since=_iso(NOW - timedelta(days=1)))

        assert result == [(ts, 100.0)]

    def test_missing_db_returns_empty_not_error(self):
        # Reads before anything was ever written (fresh deploy, empty
        # volume) must degrade to "no data", not crash the request.
        assert storage.get_bars("minute", "AAPL", since=_iso(NOW)) == []


class TestStoredRowsAndCounts:
    def test_get_stored_rows_returns_every_column_for_ticker_ordered_by_ts(self):
        t0, t1 = _iso(NOW - timedelta(days=400)), _iso(NOW)
        storage.upsert_bars("days", "AAPL", [_bar(t1, 101.0)], _iso(NOW))
        storage.upsert_bars("days", "AAPL", [_bar(t0, 100.0)], _iso(NOW))
        storage.upsert_bars("days", "MSFT", [_bar(t1, 300.0)], _iso(NOW))

        rows = storage.get_stored_rows("days", "AAPL")

        assert [(ts, price) for ts, price, _, _ in rows] == [(t0, 100.0), (t1, 101.0)]
        ts, price, analytics, recorded_at = rows[0]
        assert json.loads(analytics)["v"] == 500
        assert recorded_at == _iso(NOW)

    def test_get_stored_rows_has_no_time_window(self):
        # Unlike get_bars, nothing is cut off: an ancient row is returned.
        ancient = _iso(NOW - timedelta(days=10 * 365))
        storage.upsert_bars("minute", "AAPL", [_bar(ancient, 1.0)], _iso(NOW))

        assert [ts for ts, *_ in storage.get_stored_rows("minute", "AAPL")] == [ancient]

    def test_get_stored_rows_empty_when_db_missing_or_no_rows(self):
        assert storage.get_stored_rows("minute", "AAPL") == []
        storage.init_db()
        assert storage.get_stored_rows("minute", "AAPL") == []

    def test_get_stored_rows_rejects_unknown_tier(self):
        with pytest.raises(ValueError):
            storage.get_stored_rows("daily", "AAPL")

    def test_row_counts_covers_every_tier_for_one_ticker(self):
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 1.0), _bar(_iso(NOW - timedelta(minutes=1)), 1.0)], _iso(NOW))
        storage.upsert_bars("hour", "AAPL", [_bar(_iso(NOW), 1.0)], _iso(NOW))
        storage.upsert_bars("hour", "MSFT", [_bar(_iso(NOW), 1.0)], _iso(NOW))

        assert storage.row_counts("AAPL") == {"minute": 2, "hour": 1, "days": 0}
        assert storage.row_counts("MSFT") == {"minute": 0, "hour": 1, "days": 0}

    def test_row_counts_all_zero_when_db_missing(self):
        assert storage.row_counts("AAPL") == {"minute": 0, "hour": 0, "days": 0}


class TestLatestRecordedAt:
    def test_returns_max_recorded_at_for_ticker(self):
        storage.init_db()
        storage.upsert_bars(
            "minute",
            "AAPL",
            [_bar(_iso(NOW - timedelta(minutes=2)), 100.0)],
            _iso(NOW - timedelta(minutes=2)),
        )
        storage.upsert_bars(
            "minute", "AAPL", [_bar(_iso(NOW), 101.0)], _iso(NOW)
        )
        # Another ticker's fresher write must not leak into AAPL's answer.
        storage.upsert_bars(
            "minute",
            "MSFT",
            [_bar(_iso(NOW + timedelta(minutes=5)), 484.0)],
            _iso(NOW + timedelta(minutes=5)),
        )

        assert storage.latest_recorded_at("minute", "AAPL") == _iso(NOW)

    def test_none_when_no_rows(self):
        storage.init_db()
        assert storage.latest_recorded_at("minute", "AAPL") is None

    def test_none_when_db_missing(self):
        assert storage.latest_recorded_at("minute", "AAPL") is None


class TestPrune:
    def test_minute_rows_older_than_24h_are_deleted_boundary_kept(self):
        storage.init_db()
        cutoff = NOW - timedelta(hours=24)
        storage.upsert_bars(
            "minute",
            "AAPL",
            [
                _bar(_iso(cutoff - timedelta(seconds=1)), 99.0),  # just too old
                _bar(_iso(cutoff), 100.0),  # exactly at cutoff: survives
                _bar(_iso(cutoff + timedelta(seconds=1)), 101.0),  # inside window
            ],
            _iso(NOW),
        )

        storage.prune(NOW)

        remaining = [r[3] for r in _rows("bars_minute")]
        assert remaining == [_iso(cutoff), _iso(cutoff + timedelta(seconds=1))]

    def test_hour_rows_older_than_30d_are_deleted_boundary_kept(self):
        storage.init_db()
        cutoff = NOW - timedelta(days=30)
        storage.upsert_bars(
            "hour",
            "AAPL",
            [
                _bar(_iso(cutoff - timedelta(seconds=1)), 99.0),
                _bar(_iso(cutoff), 100.0),
                _bar(_iso(cutoff + timedelta(seconds=1)), 101.0),
            ],
            _iso(NOW),
        )

        storage.prune(NOW)

        remaining = [r[3] for r in _rows("bars_hour")]
        assert remaining == [_iso(cutoff), _iso(cutoff + timedelta(seconds=1))]

    def test_days_rows_are_never_pruned(self):
        storage.init_db()
        ancient = _iso(NOW - timedelta(days=3650))
        storage.upsert_bars("days", "AAPL", [_bar(ancient, 12.0)], _iso(NOW))

        storage.prune(NOW)

        assert [r[3] for r in _rows("bars_days")] == [ancient]

    def test_prune_applies_across_all_tickers(self):
        storage.init_db()
        old = _iso(NOW - timedelta(hours=25))
        storage.upsert_bars("minute", "AAPL", [_bar(old, 99.0)], _iso(NOW))
        storage.upsert_bars("minute", "MSFT", [_bar(old, 480.0)], _iso(NOW))

        storage.prune(NOW)

        assert _rows("bars_minute") == []


class TestLatestPrices:
    def test_returns_newest_minute_row_per_ticker(self):
        storage.init_db()
        storage.upsert_bars(
            "minute",
            "AAPL",
            [
                _bar(_iso(NOW - timedelta(minutes=2)), 100.0),
                _bar(_iso(NOW - timedelta(minutes=1)), 101.0),
            ],
            _iso(NOW),
        )
        storage.upsert_bars(
            "minute",
            "MSFT",
            [_bar(_iso(NOW - timedelta(minutes=5)), 484.0)],
            _iso(NOW - timedelta(minutes=4)),
        )

        result = storage.latest_prices(["AAPL", "MSFT", "NFLX"])

        assert result["AAPL"] == (_iso(NOW - timedelta(minutes=1)), 101.0, _iso(NOW))
        assert result["MSFT"] == (
            _iso(NOW - timedelta(minutes=5)),
            484.0,
            _iso(NOW - timedelta(minutes=4)),
        )
        # No stored rows for NFLX: absent, not None-valued.
        assert "NFLX" not in result

    def test_empty_when_db_missing(self):
        assert storage.latest_prices(["AAPL"]) == {}


class TestFetchLog:
    def test_init_db_creates_fetch_log_table(self):
        storage.init_db()
        with _connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "fetch_log" in names

    def test_record_then_read_round_trips_per_tier_and_ticker(self):
        storage.record_fetch("minute", "AAPL", _iso(NOW))
        storage.record_fetch("hour", "AAPL", _iso(NOW - timedelta(hours=1)))
        storage.record_fetch("minute", "MSFT", _iso(NOW - timedelta(minutes=5)))

        assert storage.last_fetch_at("minute", "AAPL") == _iso(NOW)
        assert storage.last_fetch_at("hour", "AAPL") == _iso(NOW - timedelta(hours=1))
        assert storage.last_fetch_at("minute", "MSFT") == _iso(NOW - timedelta(minutes=5))
        assert storage.last_fetch_at("days", "AAPL") is None

    def test_record_fetch_upserts_single_row_per_pair(self):
        storage.record_fetch("minute", "AAPL", _iso(NOW - timedelta(minutes=1)))
        storage.record_fetch("minute", "AAPL", _iso(NOW))

        assert storage.last_fetch_at("minute", "AAPL") == _iso(NOW)
        with _connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0]
        assert count == 1

    def test_fetch_log_is_independent_of_bar_rows(self):
        # Writing bars (as the leaderboard does) must not register as a fetch.
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))
        assert storage.last_fetch_at("minute", "AAPL") is None

    def test_none_when_db_missing(self):
        assert storage.last_fetch_at("minute", "AAPL") is None

    def test_rejects_unknown_tier(self):
        with pytest.raises(ValueError):
            storage.record_fetch("weekly", "AAPL", _iso(NOW))
        with pytest.raises(ValueError):
            storage.last_fetch_at("weekly", "AAPL")


class TestMeta:
    def test_init_db_creates_meta_table(self):
        storage.init_db()
        with _connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "meta" in names

    def test_get_missing_key_is_none(self):
        storage.init_db()
        assert storage.get_meta("nope") is None

    def test_none_when_db_missing(self):
        assert storage.get_meta("bars_adjustment") is None

    def test_set_then_get_round_trips_and_overwrites(self):
        storage.set_meta("k", "v1")
        assert storage.get_meta("k") == "v1"
        storage.set_meta("k", "v2")
        assert storage.get_meta("k") == "v2"
        with _connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
        assert count == 1


class TestEnsureBarsAdjustment:
    """`ensure_bars_adjustment` heals a bars-adjustment change by dropping
    the bar-tier fetch_log stamps (forcing a refetch), exactly once per
    change; bar rows and the summaries stamp are untouched."""

    def _stamp_everything(self):
        for tier in storage.TIERS:
            storage.upsert_bars(tier, "NFLX", [_bar(_iso(NOW), 1112.1)], _iso(NOW))
            storage.record_fetch(tier, "NFLX", _iso(NOW))
        storage.record_fetch(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY, _iso(NOW))

    def _bar_stamps(self) -> dict[str, str | None]:
        return {tier: storage.last_fetch_at(tier, "NFLX") for tier in storage.TIERS}

    def test_fresh_empty_db_sets_meta_without_invalidating(self):
        storage.init_db()

        assert storage.ensure_bars_adjustment("split") is False

        assert storage.get_meta(storage.BARS_ADJUSTMENT_META_KEY) == "split"
        with _connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0] == 0

    def test_works_without_prior_init_db(self):
        assert storage.ensure_bars_adjustment("split") is False
        assert storage.get_meta(storage.BARS_ADJUSTMENT_META_KEY) == "split"

    def test_legacy_db_with_bars_but_no_meta_is_invalidated(self):
        # A DB written before the meta key existed: bars are raw.
        storage.init_db()
        self._stamp_everything()

        assert storage.ensure_bars_adjustment("split") is True

        assert self._bar_stamps() == {tier: None for tier in storage.TIERS}
        # Bar rows and the summaries stamp are untouched.
        for tier in storage.TIERS:
            assert len(_rows(f"bars_{tier}")) == 1
        assert (
            storage.last_fetch_at(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY)
            == _iso(NOW)
        )
        assert storage.get_meta(storage.BARS_ADJUSTMENT_META_KEY) == "split"

    def test_changed_value_invalidates_exactly_once(self):
        storage.init_db()
        storage.ensure_bars_adjustment("raw")
        self._stamp_everything()

        assert storage.ensure_bars_adjustment("split") is True
        assert self._bar_stamps() == {tier: None for tier in storage.TIERS}

        # Re-stamp (as a refetch would) and restart: no second invalidation.
        self._stamp_everything()
        assert storage.ensure_bars_adjustment("split") is False
        assert self._bar_stamps() == {tier: _iso(NOW) for tier in storage.TIERS}

    def test_unchanged_value_is_a_noop(self):
        storage.init_db()
        storage.ensure_bars_adjustment("split")
        self._stamp_everything()

        assert storage.ensure_bars_adjustment("split") is False

        assert self._bar_stamps() == {tier: _iso(NOW) for tier in storage.TIERS}
        assert (
            storage.last_fetch_at(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY)
            == _iso(NOW)
        )

    def test_logs_info_line_when_invalidating(self, caplog):
        storage.init_db()
        self._stamp_everything()
        with caplog.at_level("INFO", logger="app.storage"):
            storage.ensure_bars_adjustment("split")
        assert any("bars adjustment changed" in r.getMessage() for r in caplog.records)


class TestUpsertBarsRecordedAtGating:
    """`recorded_at` means "when this price was last recorded": a same-price
    re-upsert leaves it alone; only a real price change (e.g. a split)
    moves it. `analytics` is always refreshed."""

    def test_new_row_is_stamped_with_the_given_recorded_at(self):
        storage.init_db()
        storage.upsert_bars("days", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))

        rows = _rows("bars_days")
        assert len(rows) == 1
        assert rows[0][4] == _iso(NOW)

    def test_same_price_keeps_recorded_at_but_refreshes_analytics(self):
        storage.init_db()
        ts = _iso(NOW - timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(ts, 100.0)], _iso(NOW))

        later = _iso(NOW + timedelta(days=1))
        same_price_more_volume = Bar(
            ts=ts,
            price=100.0,
            analytics=json.dumps(
                {"o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": 999, "vw": 100.0, "n": 9}
            ),
        )
        storage.upsert_bars("days", "AAPL", [same_price_more_volume], later)

        rows = _rows("bars_days")
        assert len(rows) == 1
        _, price, analytics, _, recorded_at = rows[0]
        assert price == pytest.approx(100.0)
        assert json.loads(analytics)["v"] == 999  # analytics refreshed
        assert recorded_at == _iso(NOW)  # stamp untouched

    def test_changed_price_replaces_price_analytics_and_recorded_at(self):
        storage.init_db()
        ts = _iso(NOW - timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(ts, 200.0)], _iso(NOW))

        later = _iso(NOW + timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(ts, 50.0)], later)  # 4:1 split

        rows = _rows("bars_days")
        assert len(rows) == 1
        _, price, analytics, _, recorded_at = rows[0]
        assert price == pytest.approx(50.0)
        assert json.loads(analytics)["c"] == pytest.approx(50.0)
        assert recorded_at == later

    def test_mixed_batch_restamps_only_the_changed_rows(self):
        storage.init_db()
        t0 = _iso(NOW - timedelta(days=2))
        t1 = _iso(NOW - timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(t0, 100.0), _bar(t1, 101.0)], _iso(NOW))

        later = _iso(NOW + timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(t0, 100.0), _bar(t1, 202.0)], later)

        by_ts = {row[3]: row for row in _rows("bars_days")}
        assert by_ts[t0][4] == _iso(NOW)
        assert by_ts[t1][4] == later
        assert by_ts[t1][1] == pytest.approx(202.0)

    def test_gating_applies_to_every_tier(self):
        storage.init_db()
        ts = _iso(NOW - timedelta(minutes=1))
        later = _iso(NOW + timedelta(minutes=1))
        for tier in storage.TIERS:
            storage.upsert_bars(tier, "AAPL", [_bar(ts, 100.0)], _iso(NOW))
            storage.upsert_bars(tier, "AAPL", [_bar(ts, 100.0)], later)
            assert _rows(f"bars_{tier}")[0][4] == _iso(NOW), tier


class TestOldestTs:
    def test_returns_min_ts_for_ticker_and_tier(self):
        storage.init_db()
        t0 = _iso(NOW - timedelta(days=3 * 365))
        t1 = _iso(NOW - timedelta(days=1))
        storage.upsert_bars("days", "AAPL", [_bar(t1, 1.0), _bar(t0, 1.0)], _iso(NOW))
        storage.upsert_bars("days", "MSFT", [_bar(_iso(NOW - timedelta(days=9 * 365)), 1.0)], _iso(NOW))
        storage.upsert_bars("hour", "AAPL", [_bar(_iso(NOW - timedelta(days=20)), 1.0)], _iso(NOW))

        assert storage.oldest_ts("days", "AAPL") == t0
        assert storage.oldest_ts("hour", "AAPL") == _iso(NOW - timedelta(days=20))

    def test_none_when_no_rows_for_ticker(self):
        storage.init_db()
        storage.upsert_bars("days", "MSFT", [_bar(_iso(NOW), 1.0)], _iso(NOW))
        assert storage.oldest_ts("days", "AAPL") is None

    def test_none_when_db_missing(self):
        assert storage.oldest_ts("days", "AAPL") is None

    def test_none_when_table_missing(self):
        # DB file exists (fetch_log only), bar table for the tier does not.
        storage.record_fetch("summaries", "*", _iso(NOW))
        with _connect() as conn:
            conn.execute("DROP TABLE IF EXISTS bars_days")
            conn.commit()
        assert storage.oldest_ts("days", "AAPL") is None

    def test_rejects_unknown_tier(self):
        with pytest.raises(ValueError):
            storage.oldest_ts("weekly", "AAPL")


CLOCK = MarketClock(
    timestamp="2026-08-20T11:59:30Z",
    is_open=False,
    next_open="2026-08-20T13:30:00Z",
    next_close="2026-08-20T20:00:00Z",
)


class TestMarketClockMeta:
    """The latest Alpaca clock lives in the `meta` table under one key
    (`MARKET_CLOCK_META_KEY`), as JSON, with the fetch stamp alongside."""

    def test_key_name(self):
        assert storage.MARKET_CLOCK_META_KEY == "market_clock"

    def test_none_when_db_missing(self):
        assert storage.get_market_clock() is None

    def test_none_when_key_missing(self):
        storage.init_db()
        assert storage.get_market_clock() is None

    def test_set_then_get_round_trips_every_field(self):
        storage.set_market_clock(CLOCK, fetched_at=_iso(NOW))

        assert storage.get_market_clock() == storage.StoredClock(
            timestamp="2026-08-20T11:59:30Z",
            is_open=False,
            next_open="2026-08-20T13:30:00Z",
            next_close="2026-08-20T20:00:00Z",
            fetched_at=_iso(NOW),
        )

    def test_stored_as_one_json_meta_row_and_overwritten(self):
        storage.set_market_clock(CLOCK, fetched_at=_iso(NOW))
        later = MarketClock(
            timestamp="2026-08-20T13:30:05Z",
            is_open=True,
            next_open="2026-08-21T13:30:00Z",
            next_close="2026-08-20T20:00:00Z",
        )
        storage.set_market_clock(later, fetched_at=_iso(NOW + timedelta(minutes=91)))

        with _connect() as conn:
            rows = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (storage.MARKET_CLOCK_META_KEY,)
            ).fetchall()
        assert len(rows) == 1
        value = json.loads(rows[0][0])
        assert value["is_open"] is True
        assert value["fetched_at"] == _iso(NOW + timedelta(minutes=91))
        stored = storage.get_market_clock()
        assert stored is not None
        assert stored.is_open is True
        assert stored.next_open == "2026-08-21T13:30:00Z"
        assert stored.fetched_at == _iso(NOW + timedelta(minutes=91))

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            json.dumps(["a", "list"]),
            json.dumps({"is_open": True}),  # missing the other fields
            json.dumps(
                {
                    "timestamp": "2026-08-20T11:59:30Z",
                    "is_open": "false",  # wrong type
                    "next_open": "2026-08-20T13:30:00Z",
                    "next_close": "2026-08-20T20:00:00Z",
                    "fetched_at": _iso(NOW),
                }
            ),
        ],
    )
    def test_corrupt_value_is_none_not_an_exception(self, raw):
        storage.set_meta(storage.MARKET_CLOCK_META_KEY, raw)

        assert storage.get_market_clock() is None

    def test_other_meta_keys_are_untouched(self):
        storage.set_meta("bars_adjustment", "split")
        storage.set_market_clock(CLOCK, fetched_at=_iso(NOW))

        assert storage.get_meta("bars_adjustment") == "split"
        with _connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
        assert count == 2


EXPECTED_TABLES = {
    "bars_minute",
    "bars_hour",
    "bars_days",
    "fetch_log",
    "summaries",
    "meta",
}


def _tables() -> set[str]:
    with _connect() as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


class TestLegacyFetchClaimsDropped:
    """`fetch_claims` (the cross-process lease) was removed; `init_db` drops
    the table once if an older build left it behind. Claims were ephemeral,
    so there is nothing to preserve."""

    _CURRENT_SCHEMA = (
        "CREATE TABLE fetch_claims ("
        "tier TEXT NOT NULL, ticker TEXT NOT NULL, claimed_at TEXT NOT NULL, "
        "claim_id TEXT NOT NULL, PRIMARY KEY (tier, ticker))"
    )
    # The even older shape, before the claim_id fencing token existed.
    _PRE_TOKEN_SCHEMA = (
        "CREATE TABLE fetch_claims ("
        "tier TEXT NOT NULL, ticker TEXT NOT NULL, claimed_at TEXT NOT NULL, "
        "PRIMARY KEY (tier, ticker))"
    )

    def _seed(self, schema: str) -> None:
        # IF NOT EXISTS / INSERT OR REPLACE so the helper works whether or
        # not the table is already there (today `init_db` still creates it).
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute(schema.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "))
            if "claim_id" in schema:
                conn.execute(
                    "INSERT OR REPLACE INTO fetch_claims VALUES (?, ?, ?, ?)",
                    ("minute", "AAPL", _iso(NOW), "f" * 32),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO fetch_claims VALUES (?, ?, ?)",
                    ("minute", "AAPL", _iso(NOW)),
                )
            conn.commit()
        assert "fetch_claims" in _tables()

    def test_current_schema_table_is_dropped_by_init_db(self):
        self._seed(self._CURRENT_SCHEMA)

        storage.init_db()

        assert "fetch_claims" not in _tables()

    def test_pre_fencing_token_table_is_dropped_too(self):
        # The old drop-and-recreate migration must not survive in any form:
        # this table is removed, not rebuilt with a claim_id column.
        self._seed(self._PRE_TOKEN_SCHEMA)

        storage.init_db()

        assert "fetch_claims" not in _tables()

    def test_drop_leaves_every_other_table_and_its_rows_intact(self):
        storage.init_db()
        storage.upsert_bars("minute", "AAPL", [_bar(_iso(NOW), 100.0)], _iso(NOW))
        storage.upsert_bars("days", "AAPL", [_bar(_iso(NOW), 101.0)], _iso(NOW))
        storage.record_fetch("minute", "AAPL", _iso(NOW))
        storage.upsert_summaries(
            [
                storage.SummaryRow(
                    ticker="AAPL",
                    price=100.0,
                    previous_close=90.0,
                    currency="USD",
                    error=None,
                )
            ],
            _iso(NOW),
        )
        storage.set_meta("bars_adjustment", "split")
        self._seed(self._CURRENT_SCHEMA)

        storage.init_db()

        assert _tables() == EXPECTED_TABLES
        assert len(_rows("bars_minute")) == 1
        assert len(_rows("bars_days")) == 1
        assert storage.last_fetch_at("minute", "AAPL") == _iso(NOW)
        assert storage.get_summaries()["AAPL"].price == 100.0
        assert storage.get_meta("bars_adjustment") == "split"

    def test_drop_is_idempotent_and_logs_once(self, caplog):
        self._seed(self._CURRENT_SCHEMA)

        with caplog.at_level("INFO", logger="app.storage"):
            storage.init_db()
            first = [r.getMessage() for r in caplog.records if "fetch_claims" in r.getMessage()]
            caplog.clear()
            storage.init_db()
            second = [r.getMessage() for r in caplog.records if "fetch_claims" in r.getMessage()]

        assert len(first) == 1
        assert "drop" in first[0].lower()
        assert second == []
        assert "fetch_claims" not in _tables()

    def test_fresh_db_has_the_expected_tables_and_no_fetch_claims(self):
        storage.init_db()

        assert _tables() == EXPECTED_TABLES


class TestLeaseApiIsGone:
    """The lease helpers and their pseudo-tier are removed outright, so a
    half-finished removal fails loudly rather than leaving dead code."""

    @pytest.mark.parametrize(
        "name",
        [
            "try_claim",
            "release_claim",
            "FETCH_CLAIMS_TABLE",
            "_ensure_fetch_claims_table",
            "CLOCK_TIER",
            "CLOCK_KEY",
        ],
    )
    def test_storage_no_longer_exposes(self, name):
        assert not hasattr(storage, name)

    def test_config_has_no_fetch_lease_setting(self):
        assert not hasattr(config, "FETCH_LEASE_SECONDS")

    def test_fetch_log_helpers_reject_the_clock_pseudo_tier(self):
        # `clock` was only ever a lease key -- the clock lives in `meta`.
        with pytest.raises(ValueError):
            storage.record_fetch("clock", "*", _iso(NOW))
        with pytest.raises(ValueError):
            storage.last_fetch_at("clock", "*")

    def test_fetch_log_helpers_still_accept_the_real_tiers(self):
        for tier in (*storage.TIERS, storage.SUMMARIES_TIER):
            storage.record_fetch(tier, "AAPL", _iso(NOW))
            assert storage.last_fetch_at(tier, "AAPL") == _iso(NOW)
        with pytest.raises(ValueError):
            storage.record_fetch("weekly", "AAPL", _iso(NOW))


class TestConcurrentWritersConverge:
    """Why dropping the lease is safe: the write paths were already
    conflict-tolerant on their own. Two writers racing costs at most one
    duplicated Alpaca call -- never a corrupt table."""

    def test_an_older_summaries_batch_never_overwrites_a_newer_one(self):
        storage.init_db()
        newer = _iso(NOW)
        older = _iso(NOW - timedelta(seconds=30))

        def row(price: float) -> storage.SummaryRow:
            return storage.SummaryRow(
                ticker="AAPL",
                price=price,
                previous_close=90.0,
                currency="USD",
                error=None,
            )

        storage.upsert_summaries([row(100.0)], newer)
        storage.upsert_summaries([row(50.0)], older)  # the slow writer lands late

        assert storage.get_summaries()["AAPL"].price == 100.0
        assert storage.last_fetch_at(storage.SUMMARIES_TIER, storage.SUMMARIES_KEY) == newer

    def test_duplicate_bar_upserts_leave_exactly_one_row(self):
        storage.init_db()
        ts = _iso(NOW)

        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.0)], _iso(NOW))
        storage.upsert_bars("minute", "AAPL", [_bar(ts, 100.0)], _iso(NOW))

        assert len(_rows("bars_minute")) == 1
