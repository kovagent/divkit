# divkit-builder

Python CLI that builds per-year Parquet shards of per-share dividend data sourced from the SEC EDGAR XBRL frames API (and optionally the `companyfacts.zip` bulk archive).

## Installation

```bash
pip install -e builder
```

Or, if you are already inside the `builder/` directory:

```bash
pip install -e .
```

The package requires Python 3.11+ and installs the `divkit-build` console script.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DIVKIT_CONTACT_EMAIL` | `divkit-builder@example.com` | Email address sent in the SEC-required `User-Agent` header. Set to a real address so the SEC WAF accepts requests. |

The SEC requires a bare `name email` User-Agent — no parentheses, no URLs. The env var is the correct place to supply your contact address; do not hard-code it.

## Commands

### `divkit-build backfill`

Full historical sweep. Fetches both dividend concepts for every quarter in the requested year range, writes one `dividends-YYYY.parquet` shard per year, and writes `manifest.json`.

```bash
# Defaults: --from-year 2009, --to-year <current year>, --out data
divkit-build backfill

# Custom range and output directory
divkit-build backfill --from-year 2015 --to-year 2024 --out /var/divkit/data

# Include the companyfacts.zip bulk archive for completeness
# (downloads ~1.4 GB to --out/companyfacts.zip, skipped if already present)
divkit-build backfill --with-bulk --out /var/divkit/data
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--from-year N` | `2009` | Earliest year to include (earliest available XBRL data). |
| `--to-year M` | current year | Latest year to include (inclusive). |
| `--out DIR` | `data` | Output directory; created if absent. |
| `--with-bulk` | off | Also merge the `companyfacts.zip` bulk archive. |

### `divkit-build nightly`

Incremental update. Fetches both dividend concepts for the current and previous quarters, merges with any existing shards in `--out`, deduplicates (Declared preferred over CashPaid for the same `(cik, period_end)`), and rewrites only the affected year shards plus a fresh `manifest.json`.

Running the same command twice on the same day with no upstream data changes produces identical shard bytes (idempotent).

```bash
# Default output directory: data
divkit-build nightly

# Custom output directory
divkit-build nightly --out /var/divkit/data
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--out DIR` | `data` | Directory containing existing shards to update. |

## Output layout

```
data/
  dividends-2009.parquet
  dividends-2010.parquet
  ...
  dividends-2025.parquet
  manifest.json
```

Each Parquet file has columns: `cik` (uint32), `ticker` (string), `period_start` (date32), `period_end` (date32), `amount` (float64), `concept` (string), `accn` (string), `form` (string).

`manifest.json` is a flat `{"dividends-YYYY.parquet": "sha256:<hex>"}` map consumed by the Rust fetcher.
