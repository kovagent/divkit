"""CLI orchestrator for divkit-builder: backfill and nightly subcommands."""

from __future__ import annotations

import argparse
import datetime
import glob
import logging
import os

import pyarrow.parquet as pq

from . import bulk, frames, schema, sec
from .frames import Row, _merge_prefer_declared

logger = logging.getLogger(__name__)

_EARLIEST_XBRL_YEAR = 2009


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_existing_rows(out_dir: str) -> list[Row]:
    """Read all dividends-*.parquet shards in *out_dir* and return them as Rows.

    Returns an empty list when no shards exist yet.
    """
    pattern = os.path.join(out_dir, "dividends-*.parquet")
    paths = sorted(glob.glob(pattern))
    if not paths:
        return []

    rows: list[Row] = []
    for path in paths:
        table = pq.read_table(path)
        d = table.to_pydict()
        for i in range(table.num_rows):
            period_start = d["period_start"][i]
            period_end = d["period_end"][i]
            # pyarrow returns datetime.date objects for date32 columns
            if isinstance(period_start, datetime.date):
                period_start = period_start.isoformat()
            if isinstance(period_end, datetime.date):
                period_end = period_end.isoformat()
            rows.append(
                Row(
                    cik=int(d["cik"][i]),
                    period_start=period_start,
                    period_end=period_end,
                    amount=float(d["amount"][i]),
                    concept=d["concept"][i],
                    accn=d["accn"][i],
                    form=d["form"][i],
                )
            )
    return rows


def _current_and_prev_quarters() -> list[tuple[int, int]]:
    """Return [(year, q), ...] for the current and previous quarters."""
    today = datetime.date.today()
    # Current quarter: q = (month - 1) // 3 + 1
    cur_q = (today.month - 1) // 3 + 1
    cur_year = today.year

    # Previous quarter
    if cur_q == 1:
        prev_q, prev_year = 4, cur_year - 1
    else:
        prev_q, prev_year = cur_q - 1, cur_year

    return [(prev_year, prev_q), (cur_year, cur_q)]


# ---------------------------------------------------------------------------
# Public functions — testable without argv
# ---------------------------------------------------------------------------

def run_backfill(
    from_year: int,
    to_year: int,
    out: str,
    with_bulk: bool,
) -> None:
    """Execute the backfill pipeline and write output shards + manifest.

    Parameters
    ----------
    from_year:
        Earliest year to include (inclusive).
    to_year:
        Latest year to include (inclusive).
    out:
        Output directory path; created if absent.
    with_bulk:
        When True, also download and merge the companyfacts.zip bulk archive.
    """
    logger.info("backfill: from_year=%d to_year=%d out=%s with_bulk=%s", from_year, to_year, out, with_bulk)

    cik_ticker = sec.cik_ticker_map()
    logger.info("backfill: loaded %d CIK→ticker mappings", len(cik_ticker))

    rows: list[Row] = frames.sweep(from_year, to_year)
    logger.info("backfill: frames sweep returned %d rows", len(rows))

    if with_bulk:
        os.makedirs(out, exist_ok=True)
        zip_path = os.path.join(out, "companyfacts.zip")
        zip_path = bulk.download(zip_path)
        bulk_rows = list(bulk.iter_company_dividends(zip_path, from_year))
        logger.info("backfill: bulk pass returned %d rows", len(bulk_rows))
        rows = _merge_prefer_declared(rows + bulk_rows)
        logger.info("backfill: merged total %d rows after dedup", len(rows))

    written = schema.write_year_shards(rows, cik_ticker, out)
    logger.info("backfill: wrote %d shard(s): %s", len(written), written)

    schema.write_manifest(out)
    logger.info("backfill: manifest written to %s/manifest.json", out)


def run_nightly(out: str) -> None:
    """Execute the nightly incremental update.

    Fetches both dividend concepts for the current and previous quarters,
    unions with existing shard data, deduplicates, and rewrites only the
    affected year shards plus a fresh manifest.

    Idempotent: re-running on the same day with no upstream data changes
    produces identical shard bytes.

    Parameters
    ----------
    out:
        Output directory containing existing shards (if any) and where updated
        shards will be written.
    """
    logger.info("nightly: starting incremental update, out=%s", out)

    cik_ticker = sec.cik_ticker_map()
    logger.info("nightly: loaded %d CIK→ticker mappings", len(cik_ticker))

    quarters = _current_and_prev_quarters()
    logger.info("nightly: fetching quarters %s", quarters)

    new_rows: list[Row] = []
    for year, q in quarters:
        for concept in (
            "CommonStockDividendsPerShareDeclared",
            "CommonStockDividendsPerShareCashPaid",
        ):
            fetched = frames.fetch_quarter(concept, year, q)
            logger.info("nightly: %s CY%dQ%d -> %d rows", concept, year, q, len(fetched))
            new_rows.extend(fetched)

    logger.info("nightly: %d new rows fetched across %d quarters", len(new_rows), len(quarters))

    existing_rows = _read_existing_rows(out)
    logger.info("nightly: %d rows read from existing shards", len(existing_rows))

    all_rows = _merge_prefer_declared(existing_rows + new_rows)
    logger.info("nightly: %d rows after union+dedup", len(all_rows))

    written = schema.write_year_shards(all_rows, cik_ticker, out)
    schema.write_manifest(out)
    logger.info("nightly: wrote %d shard(s) + manifest", len(written))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse argv and dispatch to run_backfill or run_nightly."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="divkit-build",
        description="Build divkit dividend Parquet shards from EDGAR XBRL data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------
    # backfill subcommand
    # ------------------------------------------------------------------
    backfill_parser = subparsers.add_parser(
        "backfill",
        help="Full historical sweep from --from-year to --to-year.",
    )
    backfill_parser.add_argument(
        "--from-year",
        type=int,
        default=_EARLIEST_XBRL_YEAR,
        metavar="N",
        help=f"Earliest year to include (default: {_EARLIEST_XBRL_YEAR}, earliest available XBRL).",
    )
    backfill_parser.add_argument(
        "--to-year",
        type=int,
        default=datetime.date.today().year,
        metavar="M",
        help="Latest year to include (default: current year).",
    )
    backfill_parser.add_argument(
        "--out",
        default="data",
        metavar="DIR",
        help="Output directory for Parquet shards and manifest (default: data).",
    )
    backfill_parser.add_argument(
        "--with-bulk",
        action="store_true",
        help="Also download companyfacts.zip and merge for completeness.",
    )

    # ------------------------------------------------------------------
    # nightly subcommand
    # ------------------------------------------------------------------
    nightly_parser = subparsers.add_parser(
        "nightly",
        help="Incremental update: fetch current + previous quarter, merge into existing shards.",
    )
    nightly_parser.add_argument(
        "--out",
        default="data",
        metavar="DIR",
        help="Output directory containing existing shards (default: data).",
    )

    args = parser.parse_args()

    if args.command == "backfill":
        run_backfill(
            from_year=args.from_year,
            to_year=args.to_year,
            out=args.out,
            with_bulk=args.with_bulk,
        )
    elif args.command == "nightly":
        run_nightly(out=args.out)
    else:
        parser.error(f"Unknown command: {args.command}")
