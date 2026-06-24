# divkit roadmap

This is a living document. It records what is shipped, what is planned, and what is deliberately out of scope. Dates are intentionally omitted — items ship when they are correct, not on a calendar.

## Principles

- **Zero manual work.** Every data path is fully automated: fetch, reconcile, validate, refresh nightly. No hand-curated tables.
- **Public-domain sources only.** SEC EDGAR (XBRL + filings). Nothing that cannot be redistributed in a public repo.
- **Institutional correctness over coverage.** A number that is wrong is worse than a number that is absent. The Indicated Annual Dividend rejects specials and rollup anomalies by construction; malformed input is rejected, not coerced.
- **Pure-Rust consumer.** The published crate never calls SEC at runtime and needs no API key; it reads pre-built parquet. The builder (Python) only ever runs in CI.
- **Honest labeling.** Derived or best-effort fields are documented as such. No claim of being an authoritative all-securities feed.

## Shipped (0.0.1 – 0.0.3)

- Core SDK: `Divkit` async client + blocking wrappers, free functions, `DivEvent` / `DividendSnapshot` / `Frequency` / `Concept` / `PriceProvider`.
- **Indicated Annual Dividend** (`annual_amount`): median of the last K regular payments × K (K from detected frequency: monthly/quarterly/semi-annual/annual), with a staleness gate so stopped payers decay to zero.
- `DividendCache::hydrate()` — load all data once into an in-memory index for O(1) synchronous lookups (built for high-throughput consumers).
- EDGAR pipeline: frames-API sweep + companyfacts bulk completeness pass; reconciliation of XBRL overlapping period contexts (discrete quarters vs cumulative YTD/annual rollups) into discrete payments; malformed-period rejection.
- Integrity: ETag-cached fetcher with SHA-256 manifest verification (verified on every served path), atomic cache writes, stale-cache fallback that still verifies.
- Committed dataset: ~111k reconciled dividend observations across 2009–present, every US SEC XBRL dividend filer, refreshed nightly by GitHub Actions.
- Hardening pass: a deep multi-pass code audit (concurrency, integrity, panic-safety, data-loss, cross-language interop) with fixes verified by a re-audit.

## Near-term

### Dividend dates layer — ex-dividend, record, and pay dates
The single most requested gap. SEC XBRL does **not** carry these (the us-gaap date tags exist but filers do not populate them). They live in 8-K dividend-declaration press releases as free text.

Plan (fully automated, SEC-only, best-effort by design):
- Discover dividend 8-Ks per issuer via the EDGAR full-text search API (`efts.sec.gov`, free, no key).
- Fetch the declaration exhibit; extract **record date** and **pay date** by structured parsing (regex with an LLM fallback for irregular phrasings).
- Derive **ex-dividend date** from the record date by settlement rule (T+1 era: ex = record date; earlier: record − 1 trading day), overriding with an explicitly stated ex-date when the filing gives one.
- Join to the existing dividend events by issuer + amount + period; ship as an optional layer (sibling kit or a `divkit` dates module), clearly labeled "derived from 8-K filings."

Honest scope: reliable for large-caps that file clean declaration press releases; patchier for small-cap and older filings. Coverage and accuracy will be measured and published, not claimed.

### Close the formal audit gap
Run one additional complete adversarial audit pass to reach a documented "clean pass finds nothing actionable" state, rather than the current fix → re-audit (latent-only) result.

### Nightly hardening and observability
Battle-test the nightly refresh over real runs: idempotency under partial SEC outages, drift detection on the committed dataset, and a published data-quality report (row counts, per-year coverage, manifest integrity).

## Mid-term

- **Data-quality validation suite:** spot-check IAD against published annual dividends for a known set of large-caps in CI; flag regressions.
- **Frequency edge cases:** improve detection for irregular and inconsistent-XBRL issuers (the current long tail where the annual figure is approximate).
- **Special-dividend visibility:** surface specials explicitly in the event history (kept out of IAD, but queryable) rather than only implicitly.
- **Trailing-realized vs indicated:** offer both an as-reported trailing-12-month sum and the IAD, so callers can choose.

## Exploratory

- Forward dividend estimate (next expected payment) derived from cadence + most recent rate — clearly labeled an estimate.
- Dividend yield helpers beyond `yield_on` (e.g. forward yield) once a price-provider integration pattern is settled.
- A `hydrate()` reload hook for long-running processes to pick up nightly refreshes without restart.

## Non-goals

- **Licensed corporate-actions parity.** The 100%-clean source for ex/record/pay dates is a commercial vendor feed; that is not SEC, not free, and not redistributable. divkit stays SEC-only and accepts the best-effort tradeoff for derived fields.
- **Pre-2009 history.** Structured XBRL dividend reporting did not exist before then.
- **Non-US issuers** beyond what US SEC filings report.
- **A hosted service / SLA.** divkit is a crate plus a data repository, not a managed API.

## Known limitations (current)

- Coverage is US SEC XBRL filers, 2009 onward; the most recent one or two quarters may lag until issuers file.
- Amounts and fiscal-period dates only — no ex/record/pay dates yet (see near-term).
- IAD is most accurate for regular quarterly and monthly payers; a small set of irregular or internally inconsistent XBRL filers have an approximate annual figure.
- Ticker-collision and ticker-change handling is implemented and deterministic but not yet exercised against a real collision in the committed dataset.
