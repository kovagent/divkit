"""Tests covering the four audit findings fixed in the divkit builder.

Finding 1 — Synthesized residual survives when period_end collides with a real leaf.
Finding 2 — Empty/all-filtered run leaves pre-existing shards untouched.
Finding 3 — Synthesized rows are re-validated by _is_sane_period after reconciliation.
Finding 4 — _merge_prefer_declared tiebreak is deterministic (greatest accn wins).
"""

from __future__ import annotations

import os

import pyarrow.parquet as pq
import pytest

from divkit_builder.frames import Row, _merge_prefer_declared, reconcile_periods
from divkit_builder import schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    cik: int,
    start: str,
    end: str,
    amount: float,
    concept: str = "Declared",
    accn: str = "0001000001-24-000001",
    form: str | None = None,
    synthesized: bool = False,
) -> Row:
    return Row(
        cik=cik,
        period_start=start,
        period_end=end,
        amount=amount,
        concept=concept,
        accn=accn,
        form=form,
        synthesized=synthesized,
    )


# ---------------------------------------------------------------------------
# Finding 1 — Full-coverage residual survives period_end collision
#
# Scenario: annual 2024-01-01..2024-12-31 = 2.5
#   four end-aligned quarters of 0.5 each (Q1..Q4 all present, all ending
#   at their quarter boundaries, with Q4 ending on 2024-12-31 == container end).
#   Container value (2.5) > leaf sum (2.0) → residual = 0.5.
#   Synth gets period_end == container.period_end == 2024-12-31 == Q4.period_end.
#   After write_year_shards the discrete total must be 2.5, not 2.0.
# ---------------------------------------------------------------------------

def test_finding1_residual_survives_period_end_collision(tmp_path):
    """Synthesized 0.5 residual survives even when synth.period_end == a real leaf's period_end."""
    q1 = _row(1001, "2024-01-01", "2024-03-31", 0.5, accn="0001000001-24-000001")
    q2 = _row(1001, "2024-04-01", "2024-06-30", 0.5, accn="0001000001-24-000002")
    q3 = _row(1001, "2024-07-01", "2024-09-30", 0.5, accn="0001000001-24-000003")
    q4 = _row(1001, "2024-10-01", "2024-12-31", 0.5, accn="0001000001-24-000004")
    # Annual value is 2.5 — 0.5 more than the four quarters sum to (2.0).
    # The synth falls back to the container's full span because there is no tail gap
    # (last leaf's period_end == container.period_end == 2024-12-31), giving
    # synth.period_end == 2024-12-31 which collides with q4.period_end.
    annual = _row(1001, "2024-01-01", "2024-12-31", 2.5, accn="0001000001-24-000005")

    paths = schema.write_year_shards(
        [q1, q2, q3, q4, annual],
        {1001: "TEST"},
        str(tmp_path),
    )

    assert paths, "Expected at least one shard written"
    table = pq.read_table(str(tmp_path / "dividends-2024.parquet"))
    d = table.to_pydict()

    cik_amounts = [a for cik, a in zip(d["cik"], d["amount"]) if cik == 1001]
    total = round(sum(cik_amounts), 6)
    assert abs(total - 2.5) < 1e-4, (
        f"Discrete total must be 2.5 (residual survives); got {total} from {cik_amounts}"
    )


# ---------------------------------------------------------------------------
# Finding 1 (unit) — synthesized field is set and dedup key includes it
# ---------------------------------------------------------------------------

def test_finding1_synthesized_flag_set():
    """reconcile_periods sets synthesized=True on recovered residual rows."""
    q1 = _row(2001, "2024-01-01", "2024-03-31", 0.5)
    q2 = _row(2001, "2024-04-01", "2024-06-30", 0.5)
    q3 = _row(2001, "2024-07-01", "2024-09-30", 0.5)
    # 3 quarters at 0.5 each = 1.5; annual = 2.5; residual = 1.0
    annual = _row(2001, "2024-01-01", "2024-12-31", 2.5)

    result = reconcile_periods([q1, q2, q3, annual])
    synths = [r for r in result if r.synthesized]
    assert len(synths) == 1, f"Expected exactly 1 synthesized row, got {synths}"
    assert synths[0].amount == pytest.approx(1.0, abs=1e-4)


def test_finding1_dedup_key_includes_synthesized():
    """_merge_prefer_declared keeps a synthesized row separate from a real row at the same (cik, period_end)."""
    real = _row(3001, "2024-10-01", "2024-12-31", 0.5, synthesized=False)
    synth = _row(3001, "2024-10-01", "2024-12-31", 0.3, synthesized=True)

    merged = _merge_prefer_declared([real, synth])
    assert len(merged) == 2, (
        f"Synthesized row must not be coalesced with real row; got {len(merged)} rows"
    )
    amounts = {r.synthesized: r.amount for r in merged}
    assert amounts[False] == 0.5
    assert amounts[True] == 0.3


# ---------------------------------------------------------------------------
# Finding 2 — Empty-write run leaves existing shards intact
# ---------------------------------------------------------------------------

def test_finding2_all_malformed_input_leaves_shards_intact(tmp_path):
    """All-malformed input produces zero writes; pre-existing shards must NOT be deleted."""
    # Write a valid shard first
    good_row = _row(4001, "2023-01-01", "2023-03-31", 0.5, accn="0001000001-23-000001")
    schema.write_year_shards([good_row], {4001: "EXIST"}, str(tmp_path))
    existing_shard = str(tmp_path / "dividends-2023.parquet")
    assert os.path.exists(existing_shard), "Pre-condition: shard must exist before test"

    # Now pass all-malformed rows (inverted dates → all rejected by _is_sane_period)
    bad1 = _row(4001, "2023-03-31", "2023-01-01", 0.5)  # end < start
    bad2 = _row(4001, "2018-05-10", "2108-05-10", 0.5)  # far-future typo year

    result = schema.write_year_shards([bad1, bad2], {4001: "EXIST"}, str(tmp_path))
    assert result == [], f"Expected no shards written for all-malformed input; got {result}"
    assert os.path.exists(existing_shard), (
        "Pre-existing shard must NOT be deleted when write run produces zero output"
    )


# ---------------------------------------------------------------------------
# Finding 3 — Synthesized rows are re-validated by _is_sane_period
# ---------------------------------------------------------------------------

def test_finding3_insane_synth_is_filtered(tmp_path):
    """Synthesized row that fails _is_sane_period is filtered out before writing.

    We construct a pathological container whose dates are borderline-valid (just
    within the 400-day limit) but whose full-span fallback synth would not be
    written if it somehow exceeded the limit.  Instead we directly probe that the
    post-reconcile filter works by injecting a container that produces a synth with
    an implausibly long span via the no-contained-leaves path.

    Simpler: just verify the direct path — a synth Row with an insane period
    produced by a hypothetical reconcile is rejected by the schema pipeline,
    while a same-CIK sane row still lands in the shard.
    """
    from divkit_builder.schema import _is_sane_period

    # Construct what a "bad synth" would look like (> 400 days span)
    bad_synth = Row(
        cik=5001,
        period_start="2020-01-01",
        period_end="2023-01-01",  # 3 years — > 400 days
        amount=9.99,
        concept="Declared",
        accn="0001000001-24-000099",
        form=None,
        synthesized=True,
    )
    assert not _is_sane_period(bad_synth), "Pre-condition: bad synth must fail sanity check"

    good_row = _row(5001, "2024-01-01", "2024-03-31", 0.5)
    # write_year_shards applies _is_sane_period to all rows including synthesized ones
    schema.write_year_shards([bad_synth, good_row], {5001: "SYN"}, str(tmp_path))

    table = pq.read_table(str(tmp_path / "dividends-2024.parquet"))
    d = table.to_pydict()
    amounts = [a for cik, a in zip(d["cik"], d["amount"]) if cik == 5001]
    assert 9.99 not in amounts, "Insane synthesized row must be filtered out"
    assert 0.5 in amounts, "Valid row must still be present"


def test_finding3_synth_passes_sanity_survives(tmp_path):
    """A synthesized row with a sane period survives the post-reconcile filter."""
    q1 = _row(6001, "2024-01-01", "2024-03-31", 0.5, accn="0001000001-24-000001")
    q2 = _row(6001, "2024-04-01", "2024-06-30", 0.5, accn="0001000001-24-000002")
    q3 = _row(6001, "2024-07-01", "2024-09-30", 0.5, accn="0001000001-24-000003")
    annual = _row(6001, "2024-01-01", "2024-12-31", 2.5, accn="0001000001-24-000010")

    schema.write_year_shards([q1, q2, q3, annual], {6001: "SYN2"}, str(tmp_path))
    table = pq.read_table(str(tmp_path / "dividends-2024.parquet"))
    d = table.to_pydict()
    cik_amounts = [a for cik, a in zip(d["cik"], d["amount"]) if cik == 6001]
    assert abs(sum(cik_amounts) - 2.5) < 1e-4, (
        f"Sane synthesized row must survive; total={sum(cik_amounts)}"
    )


# ---------------------------------------------------------------------------
# Finding 4 — _merge_prefer_declared tiebreak is deterministic (greatest accn)
# ---------------------------------------------------------------------------

def test_finding4_accn_tiebreak_deterministic_regardless_of_order():
    """Same-concept collision: greatest accn wins, independent of input order."""
    older = _row(7001, "2024-01-01", "2024-03-31", 0.4, concept="Declared",
                 accn="0001000001-24-000001")
    newer = _row(7001, "2024-01-01", "2024-03-31", 0.5, concept="Declared",
                 accn="0001000001-24-000099")

    result_a = _merge_prefer_declared([older, newer])
    result_b = _merge_prefer_declared([newer, older])

    assert len(result_a) == 1
    assert len(result_b) == 1
    assert result_a[0].accn == "0001000001-24-000099", (
        f"Greatest accn must win; got {result_a[0].accn}"
    )
    assert result_b[0].accn == "0001000001-24-000099", (
        f"Result must be the same regardless of input order; got {result_b[0].accn}"
    )
    assert result_a[0].amount == result_b[0].amount == 0.5


def test_finding4_declared_still_beats_cashpaid_regardless_of_accn():
    """Declared beats CashPaid even when CashPaid has a greater accn."""
    declared = _row(8001, "2024-01-01", "2024-03-31", 0.5, concept="Declared",
                    accn="0001000001-24-000001")  # lower accn
    cashpaid = _row(8001, "2024-01-01", "2024-03-31", 0.4, concept="CashPaid",
                    accn="0001000001-24-000099")  # higher accn but wrong concept

    result_a = _merge_prefer_declared([declared, cashpaid])
    result_b = _merge_prefer_declared([cashpaid, declared])

    assert len(result_a) == 1
    assert len(result_b) == 1
    assert result_a[0].concept == "Declared"
    assert result_b[0].concept == "Declared"


def test_finding4_cashpaid_tiebreak_deterministic():
    """Same-concept CashPaid collision: greatest accn wins, order-independent."""
    older = _row(9001, "2024-04-01", "2024-06-30", 0.3, concept="CashPaid",
                 accn="0001000001-24-000005")
    newer = _row(9001, "2024-04-01", "2024-06-30", 0.35, concept="CashPaid",
                 accn="0001000001-24-000050")

    result_a = _merge_prefer_declared([older, newer])
    result_b = _merge_prefer_declared([newer, older])

    assert len(result_a) == 1
    assert len(result_b) == 1
    assert result_a[0].accn == result_b[0].accn == "0001000001-24-000050"
