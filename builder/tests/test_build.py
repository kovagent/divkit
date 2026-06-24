"""Tests for builder/divkit_builder/build.py — CLI orchestrator (no network)."""

from __future__ import annotations

import json

import pyarrow.parquet as pq

from divkit_builder.frames import Row
from divkit_builder import build


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXED_ROWS = [
    Row(
        cik=100,
        period_start="2024-01-01",
        period_end="2024-03-31",
        amount=0.50,
        concept="Declared",
        accn="0001-01-2024",
        form="10-Q",
    ),
    Row(
        cik=200,
        period_start="2024-04-01",
        period_end="2024-06-30",
        amount=0.75,
        concept="CashPaid",
        accn="0002-02-2024",
        form=None,
    ),
]

_CIK_TICKER = {100: "AAPL", 200: "MSFT"}


# ---------------------------------------------------------------------------
# Backfill smoke test
# ---------------------------------------------------------------------------

def test_backfill_smoke(tmp_path, monkeypatch):
    """run_backfill writes dividends-2024.parquet + manifest.json without network."""
    monkeypatch.setattr("divkit_builder.build.frames.sweep", lambda from_year, to_year: list(_FIXED_ROWS))
    monkeypatch.setattr("divkit_builder.build.sec.cik_ticker_map", lambda: dict(_CIK_TICKER))

    build.run_backfill(from_year=2024, to_year=2024, out=str(tmp_path), with_bulk=False)

    shard = tmp_path / "dividends-2024.parquet"
    manifest = tmp_path / "manifest.json"

    assert shard.exists(), "dividends-2024.parquet not written"
    assert manifest.exists(), "manifest.json not written"

    # Parquet must have 2 rows (one per CIK)
    table = pq.read_table(str(shard))
    assert table.num_rows == 2

    # Manifest must be flat {filename: "sha256:..."} format
    data = json.loads(manifest.read_text())
    assert isinstance(data, dict)
    assert "dividends-2024.parquet" in data
    assert data["dividends-2024.parquet"].startswith("sha256:")


# ---------------------------------------------------------------------------
# Nightly idempotency test
# ---------------------------------------------------------------------------

def test_nightly_idempotency(tmp_path, monkeypatch):
    """run_nightly produces the same shard bytes on a second run (same day)."""
    # Pre-populate out_dir with a 2024 shard so nightly can read existing rows
    from divkit_builder import schema
    schema.write_year_shards(_FIXED_ROWS, _CIK_TICKER, str(tmp_path))
    schema.write_manifest(str(tmp_path))

    # fetch_quarter returns two rows covering a quarter in 2024
    nightly_row = Row(
        cik=300,
        period_start="2024-07-01",
        period_end="2024-09-30",
        amount=1.25,
        concept="Declared",
        accn="0003-03-2024",
        form="10-Q",
    )
    monkeypatch.setattr("divkit_builder.build.frames.fetch_quarter", lambda concept, year, q: [nightly_row])
    monkeypatch.setattr("divkit_builder.build.sec.cik_ticker_map", lambda: dict(_CIK_TICKER))

    build.run_nightly(out=str(tmp_path))
    shard_after_run1 = (tmp_path / "dividends-2024.parquet").read_bytes()

    # Second run — same monkeypatched fetch_quarter returns same data
    build.run_nightly(out=str(tmp_path))
    shard_after_run2 = (tmp_path / "dividends-2024.parquet").read_bytes()

    assert shard_after_run1 == shard_after_run2, "Second nightly run should not change shard bytes"
