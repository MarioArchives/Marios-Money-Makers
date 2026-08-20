"""Tests for app.cache.TTLCacheWithLock.

Covers the three behaviors called out in the plan's Stage 2 test list for
`cache.py`:
  1. Cached value served within TTL without re-invoking the fetch function.
  2. Cache expires after TTL and triggers a recompute.
  3. Per-key lock collapses concurrent cache-miss callers into a single
     underlying fetch.

Plus a couple of tests for `get_stale`, which the plan's suggested API
calls out explicitly as backing the serve-stale-on-error flow.

No network calls are made anywhere in this module.
"""

from __future__ import annotations

import asyncio

import pytest

from app.cache import TTLCacheWithLock

pytestmark = pytest.mark.asyncio


async def test_cached_value_served_within_ttl_without_refetch():
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        return "value"

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=60.0)

    first = await cache.get_or_fetch("k", fetch)
    second = await cache.get_or_fetch("k", fetch)

    assert first == "value"
    assert second == "value"
    assert calls == 1, "fetch should be invoked once for two calls within TTL"


async def test_cache_expires_after_ttl_and_triggers_recompute():
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=0.05)

    first = await cache.get_or_fetch("k", fetch)
    await asyncio.sleep(0.15)  # let the TTL window elapse
    second = await cache.get_or_fetch("k", fetch)

    assert first == "value-1"
    assert second == "value-2"
    assert calls == 2, "fetch should be re-invoked after the TTL expires"


async def test_concurrent_cache_miss_callers_collapse_into_one_fetch():
    calls = 0

    async def slow_fetch() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return "value"

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=60.0)

    results = await asyncio.gather(*(cache.get_or_fetch("k", slow_fetch) for _ in range(8)))

    assert calls == 1, "concurrent misses on the same key must share one fetch"
    assert all(result == "value" for result in results)


async def test_concurrent_misses_on_different_keys_each_fetch_independently():
    calls_by_key: dict[str, int] = {"a": 0, "b": 0}

    def make_fetch(key: str):
        async def fetch() -> str:
            calls_by_key[key] += 1
            await asyncio.sleep(0.05)
            return f"value-{key}"

        return fetch

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=60.0)

    results = await asyncio.gather(
        cache.get_or_fetch("a", make_fetch("a")),
        cache.get_or_fetch("b", make_fetch("b")),
        cache.get_or_fetch("a", make_fetch("a")),
        cache.get_or_fetch("b", make_fetch("b")),
    )

    assert calls_by_key == {"a": 1, "b": 1}
    assert set(results) == {"value-a", "value-b"}


async def test_get_stale_returns_none_when_key_never_cached():
    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=60.0)

    assert cache.get_stale("missing") is None


async def test_get_stale_returns_last_value_after_ttl_expiry():
    async def fetch() -> str:
        return "cached-value"

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=0.05)

    await cache.get_or_fetch("k", fetch)
    await asyncio.sleep(0.15)  # value now expired out of the fresh cache

    # get_stale must still be able to hand back the last known value even
    # though it is no longer "fresh" per the TTL.
    assert cache.get_stale("k") == "cached-value"


async def test_invalidate_clears_cached_value():
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    cache: TTLCacheWithLock[str] = TTLCacheWithLock(ttl=60.0)

    first = await cache.get_or_fetch("k", fetch)
    cache.invalidate("k")
    second = await cache.get_or_fetch("k", fetch)

    assert first == "value-1"
    assert second == "value-2"
    assert calls == 2, "invalidate should force the next call to refetch"
