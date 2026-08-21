"""Shared pytest fixtures.

The app's lifespan hook starts the `app.backfill` sweep as a background
task whenever `app.config.BACKFILL_ENABLED` is on. Tests that enter the
lifespan (`with TestClient(app)`) must not kick off a real sweep -- it
would hit (mocked or not) Alpaca for all 60 pairs -- so it is disabled
for every test here; `tests/test_backfill.py` re-enables it where the
sweep itself is under test.
"""

from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _disable_backfill(monkeypatch):
    monkeypatch.setattr(config, "BACKFILL_ENABLED", False)
    yield
