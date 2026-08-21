"""Tests for `app.storage` — the three-table SQLite backup store.

Every test runs against its own tmp-path DB by monkeypatching
`app.config.DB_PATH` (which `storage` reads at call time, per its module
contract). No mocking: this is the real persistence layer.

Row contract (all three tables): ticker, price, analytics (JSON string of
the raw Alpaca bar fields), ts (bar time, ISO-8601 UTC "...Z"), and
recorded_at (fetch time, same format), with PRIMARY KEY (ticker, ts).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import config, storage
from app.alpaca_client import Bar

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
        assert {"bars_minute", "bars_hour", "bars_month"} <= tables

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
        # current month's bar keeps changing until the month closes.
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
        assert _rows("bars_month") == []

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
        storage.upsert_bars("month", "AAPL", [_bar(t1, 101.0)], _iso(NOW))
        storage.upsert_bars("month", "AAPL", [_bar(t0, 100.0)], _iso(NOW))
        storage.upsert_bars("month", "MSFT", [_bar(t1, 300.0)], _iso(NOW))

        rows = storage.get_stored_rows("month", "AAPL")

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

        assert storage.row_counts("AAPL") == {"minute": 2, "hour": 1, "month": 0}
        assert storage.row_counts("MSFT") == {"minute": 0, "hour": 1, "month": 0}

    def test_row_counts_all_zero_when_db_missing(self):
        assert storage.row_counts("AAPL") == {"minute": 0, "hour": 0, "month": 0}


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

    def test_month_rows_are_never_pruned(self):
        storage.init_db()
        ancient = _iso(NOW - timedelta(days=3650))
        storage.upsert_bars("month", "AAPL", [_bar(ancient, 12.0)], _iso(NOW))

        storage.prune(NOW)

        assert [r[3] for r in _rows("bars_month")] == [ancient]

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
        assert storage.last_fetch_at("month", "AAPL") is None

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
    """`ensure_bars_adjustment` heals DBs whose bars were fetched under a
    different Alpaca `adjustment` mode by dropping the bar-tier fetch_log
    stamps (so the next request/sweep refetches and overwrites) -- exactly
    once per change, never touching bar rows or the summaries stamp."""

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
