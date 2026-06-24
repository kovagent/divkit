"""Tests for builder/divkit_builder/bulk.py — companyfacts.zip completeness pass."""

from __future__ import annotations

import json
import zipfile
from contextlib import contextmanager


def test_iter_company_dividends_from_zip(tmp_path):
    """iter_company_dividends yields rows from a synthetic companyfacts.zip."""
    from divkit_builder import bulk

    facts = {
        "cik": 21344,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-03-31",
                                "val": 0.485,
                                "accn": "a",
                                "form": "10-Q",
                            }
                        ]
                    }
                }
            }
        },
    }
    zp = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000021344.json", json.dumps(facts))

    rows = list(bulk.iter_company_dividends(str(zp), from_year=2009))
    assert len(rows) == 1
    assert rows[0].cik == 21344
    assert rows[0].amount == 0.485


def test_iter_company_dividends_year_filter(tmp_path):
    """Entries whose end year < from_year are filtered out."""
    from divkit_builder import bulk

    facts = {
        "cik": 21344,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            # This entry is in 2008 — should be filtered when from_year=2009
                            {
                                "start": "2008-01-01",
                                "end": "2008-03-31",
                                "val": 0.300,
                                "accn": "b",
                                "form": "10-Q",
                            },
                            # This entry is in 2009 — should be included
                            {
                                "start": "2009-01-01",
                                "end": "2009-03-31",
                                "val": 0.350,
                                "accn": "c",
                                "form": "10-Q",
                            },
                        ]
                    }
                }
            }
        },
    }
    zp = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000021344.json", json.dumps(facts))

    rows = list(bulk.iter_company_dividends(str(zp), from_year=2009))
    assert len(rows) == 1
    assert rows[0].amount == 0.350


def test_iter_company_dividends_both_concepts(tmp_path):
    """Both Declared and CashPaid concepts are extracted."""
    from divkit_builder import bulk

    facts = {
        "cik": 99999,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-03-31",
                                "val": 0.50,
                                "accn": "x",
                                "form": "10-Q",
                            }
                        ]
                    }
                },
                "CommonStockDividendsPerShareCashPaid": {
                    "units": {
                        "USD/shares": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-03-31",
                                "val": 0.48,
                                "accn": "y",
                                "form": "10-Q",
                            }
                        ]
                    }
                },
            }
        },
    }
    zp = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000099999.json", json.dumps(facts))

    rows = list(bulk.iter_company_dividends(str(zp), from_year=2020))
    assert len(rows) == 2
    concepts = {r.concept for r in rows}
    assert "Declared" in concepts
    assert "CashPaid" in concepts


def test_iter_company_dividends_malformed_skipped(tmp_path):
    """A malformed company JSON is skipped without aborting iteration."""
    from divkit_builder import bulk

    good_facts = {
        "cik": 11111,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-03-31",
                                "val": 1.00,
                                "accn": "z",
                                "form": "10-Q",
                            }
                        ]
                    }
                }
            }
        },
    }
    zp = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000011111.json", json.dumps(good_facts))
        z.writestr("CIK0000022222.json", "NOT VALID JSON{{{{")

    rows = list(bulk.iter_company_dividends(str(zp), from_year=2020))
    assert len(rows) == 1
    assert rows[0].cik == 11111


class _FakeResponse:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=None):
        yield self._payload


def test_download_atomic_write(tmp_path, monkeypatch):
    """download streams to dest atomically; no .part file remains afterward."""
    from divkit_builder import bulk

    payload = b"hello-companyfacts"

    @contextmanager
    def fake_stream(method, url, **kwargs):
        yield _FakeResponse(payload)

    monkeypatch.setattr(bulk.httpx, "stream", fake_stream)

    dest = tmp_path / "companyfacts.zip"
    result = bulk.download(str(dest))

    assert result == str(dest)
    assert dest.exists()
    assert dest.read_bytes() == payload
    assert not (tmp_path / "companyfacts.zip.part").exists()


def test_download_skips_when_present(tmp_path, monkeypatch):
    """download returns early without streaming when dest already exists non-empty."""
    from divkit_builder import bulk

    def boom(*args, **kwargs):
        raise AssertionError("httpx.stream should not be called when dest exists")

    monkeypatch.setattr(bulk.httpx, "stream", boom)

    dest = tmp_path / "companyfacts.zip"
    dest.write_bytes(b"already here")

    result = bulk.download(str(dest))
    assert result == str(dest)
    assert dest.read_bytes() == b"already here"


def test_iter_company_dividends_missing_start_falls_back_to_end(tmp_path):
    """An entry with no 'start' key yields a row with period_start == period_end (no crash)."""
    from divkit_builder import bulk

    facts = {
        "cik": 55555,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            # No 'start' key — XBRL instant fact
                            {
                                "end": "2024-03-31",
                                "val": 0.485,
                                "accn": "a",
                                "form": "10-Q",
                            }
                        ]
                    }
                }
            }
        },
    }
    zp = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000055555.json", json.dumps(facts))

    # Must not raise; period_start should fall back to period_end
    rows = list(bulk.iter_company_dividends(str(zp), from_year=2009))
    assert len(rows) == 1
    assert rows[0].period_start == rows[0].period_end == "2024-03-31"
    assert rows[0].amount == 0.485


def test_download_removes_partial_on_failure(tmp_path, monkeypatch):
    """A mid-stream failure removes the .part file and leaves no dest."""
    from divkit_builder import bulk

    @contextmanager
    def failing_stream(method, url, **kwargs):
        class _Resp:
            def raise_for_status(self):
                return None

            def iter_bytes(self, chunk_size=None):
                yield b"partial"
                raise RuntimeError("connection dropped")

        yield _Resp()

    monkeypatch.setattr(bulk.httpx, "stream", failing_stream)

    dest = tmp_path / "companyfacts.zip"
    try:
        bulk.download(str(dest))
    except RuntimeError:
        pass
    else:  # pragma: no cover - the stream is expected to raise
        raise AssertionError("expected RuntimeError to propagate")

    assert not dest.exists()
    assert not (tmp_path / "companyfacts.zip.part").exists()
