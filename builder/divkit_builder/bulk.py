"""companyfacts.zip bulk completeness pass for per-share dividend concepts."""

from __future__ import annotations

import json
import logging
import os
import zipfile
from collections.abc import Iterator

import httpx

from .frames import Row
from .sec import user_agent

logger = logging.getLogger(__name__)

_COMPANYFACTS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
)

_CONCEPTS = (
    ("CommonStockDividendsPerShareDeclared", "Declared"),
    ("CommonStockDividendsPerShareCashPaid", "CashPaid"),
)


# ---------------------------------------------------------------------------
# Bulk zip iterator
# ---------------------------------------------------------------------------

def iter_company_dividends(zip_path: str, from_year: int) -> Iterator[Row]:
    """Yield :class:`~frames.Row` objects from *zip_path* (``companyfacts.zip``).

    Streams the zip one member at a time — does not load the full archive into
    RAM.  For each ``CIK*.json`` member both dividend concepts are extracted:

    * ``CommonStockDividendsPerShareDeclared`` → ``concept="Declared"``
    * ``CommonStockDividendsPerShareCashPaid``  → ``concept="CashPaid"``

    Only entries whose ``end`` year is >= *from_year* are yielded.  Malformed
    members are logged and skipped so one bad company never aborts the sweep.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("CIK") and name.endswith(".json")):
                continue
            try:
                raw = zf.read(name)
                facts = json.loads(raw)
                cik = int(facts["cik"])
                usgaap = facts["facts"]["us-gaap"]
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("bulk: skipping %s — %s", name, exc)
                continue

            for concept_key, concept_label in _CONCEPTS:
                try:
                    units = usgaap[concept_key]["units"]["USD/shares"]
                except KeyError:
                    # Company does not report this concept — normal, skip quietly.
                    continue

                for entry in units:
                    try:
                        end: str = entry["end"]
                        if int(end[:4]) < from_year:
                            continue
                        # period_start is optional in XBRL instant facts; fall
                        # back to period_end so downstream date parsing never
                        # receives an empty string.
                        period_start: str = entry.get("start") or end
                        yield Row(
                            cik=cik,
                            period_start=period_start,
                            period_end=end,
                            amount=float(entry["val"]),
                            concept=concept_label,
                            accn=entry.get("accn", ""),
                            form=entry.get("form"),
                        )
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.warning(
                            "bulk: skipping entry in %s/%s — %s",
                            name,
                            concept_key,
                            exc,
                        )
                        continue


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def download(dest: str) -> str:
    """Stream-download ``companyfacts.zip`` to *dest* and return *dest*.

    Skips the download if *dest* already exists and is non-empty.  Uses
    ``httpx`` with ``stream=True`` and a long timeout suitable for the ~1.4 GB
    file.  The SEC User-Agent header is sent in the bare ``divkit <email>``
    form required by the SEC WAF.
    """
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        logger.info("bulk.download: %s already present, skipping", dest)
        return dest

    logger.info("bulk.download: fetching %s -> %s", _COMPANYFACTS_URL, dest)
    headers = {"User-Agent": user_agent()}
    # Large file — allow up to 30 minutes for the download.
    timeout = httpx.Timeout(connect=30.0, read=1800.0, write=None, pool=30.0)

    # Stream into a sibling temp file and atomically rename only on success, so
    # a download killed mid-stream never leaves a truncated *dest* that a later
    # run would reuse as if complete.
    tmp = dest + ".part"
    try:
        with httpx.stream("GET", _COMPANYFACTS_URL, headers=headers, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):  # 1 MiB chunks
                    fh.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        # Remove the partial file so the next run starts clean.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    logger.info("bulk.download: wrote %d bytes to %s", os.path.getsize(dest), dest)
    return dest
