"""Tests for builder/divkit_builder/bulk.py — companyfacts.zip completeness pass."""

from __future__ import annotations

import json
import zipfile


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
