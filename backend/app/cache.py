"""Async TTL cache with per-key locking.

Wraps `cachetools.TTLCache` with an `asyncio.Lock` per key so that
concurrent cache-miss callers for the same key collapse into a single
underlying fetch (thundering-herd protection), per the plan's data-flow
section. Also exposes a way to peek a stale (expired-but-previously-cached)
value so callers can serve last-known-good data when a fresh fetch fails.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, Generic, Optional, TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class TTLCacheWithLock(Generic[T]):
    """Async-safe TTL cache that collapses concurrent cache-miss fetches.

    - `get_or_fetch` returns the cached value for `key` if it is still
      fresh (within `ttl` seconds of being stored); otherwise it awaits
      `fetch()` to compute a new value, stores it, and returns it.
    - Concurrent callers that miss on the same `key` at the same time must
      share a single in-flight call to `fetch()` rather than each starting
      their own (a per-key `asyncio.Lock` is used for this).
    - `get_stale` returns the most recently cached value for `key` even if
      it has since expired from the TTL cache, or `None` if nothing has
      ever been cached for that key. This backs the "serve stale on fetch
      error" flow described in the plan's error-handling section.
    """

    def __init__(self, ttl: float, maxsize: int = 128) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._locks: Dict[str, asyncio.Lock] = {}
        # Shadow store of the last successfully cached value per key,
        # independent of TTL expiry, used to serve stale reads.
        self._stale: Dict[str, T] = {}

    def _get_lock(self, key: str) -> asyncio.Lock:
        """Return the `asyncio.Lock` for `key`, creating it if necessary."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_or_fetch(self, key: str, fetch: Callable[[], Awaitable[T]]) -> T:
        """Return a fresh value for `key`, invoking `fetch` at most once per miss.

        On a cache hit within the TTL window, `fetch` is not called at all.
        On a miss, `fetch` is awaited under this key's lock so that other
        concurrent callers who also missed wait for and reuse the same
        result instead of each calling `fetch` themselves.
        """
        try:
            return self._cache[key]
        except KeyError:
            pass

        lock = self._get_lock(key)
        async with lock:
            # Another caller may have populated the cache while we were
            # waiting for the lock; re-check before fetching.
            try:
                return self._cache[key]
            except KeyError:
                pass

            value = await fetch()
            self._cache[key] = value
            self._stale[key] = value
            return value

    def get_stale(self, key: str) -> Optional[T]:
        """Return the last cached value for `key`, even if it has expired.

        Returns `None` if no value has ever been cached for `key`.
        """
        return self._stale.get(key)

    def invalidate(self, key: str) -> None:
        """Drop any cached (and stale-shadow) value for `key`."""
        self._cache.pop(key, None)
        self._stale.pop(key, None)
