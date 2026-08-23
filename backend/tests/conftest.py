"""Shared pytest fixtures. `BACKFILL_ENABLED` is force-disabled for every
test so entering the app lifespan doesn't kick off a real sweep;
`test_backfill.py` re-enables it where the sweep itself is under test."""

from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _disable_backfill(monkeypatch):
    monkeypatch.setattr(config, "BACKFILL_ENABLED", False)
    yield
