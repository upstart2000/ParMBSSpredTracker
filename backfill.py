"""
One-time historical backfill of daily_spreads from FINRA's monthly zip
archives (https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-YYYYMM.zip).

Scoped to 2026 only: months are probed sequentially (202601, 202602, ...) and
the run stops at the first month whose zip isn't published yet. This also
keeps settlement-month selection simple, since settlement_calendar.py's
CLASS_A_SETTLEMENT_DATES_2026 only covers 2026 - no need to reconcile a
different year's settlement calendar here.

Each monthly zip contains one FINRA_IDS_PXTABLES-YYYYMMDD.xlsx (plus a
FINRA_IDS_STAR-YYYYMMDD.xlsx we don't need) per trading day. Each is parsed
in-memory (no need to unzip to disk) and upserted via the same
pipeline.build_daily_record() the nightly job uses.
"""
import argparse
import io
import logging
import re
import sys
from datetime import datetime

import requests

import db
from finra_parser import get_data_as_of_date
from pipeline import build_daily_record

MONTHLY_ZIP_URL = "https://cdn.finra.org/trace/ids/monthly/HISTORIC_SPREPORTS-{yyyymm}.zip"
PXTABLES_NAME_RE = re.compile(r"^FINRA_IDS_PXTABLES-(\d{8})\.xlsx$")
BACKFILL_YEAR = 2026

logger = logging.getLogger("backfill")


def available_months(year=BACKFILL_YEAR):
    """
    Probes HISTORIC_SPREPORTS-<year><01..12>.zip and returns the yyyymm strings
    that exist, stopping at the first missing month (months publish in order,
    only after that month has fully closed).
    """
    months = []
    for mm in range(1, 13):
        yyyymm = f"{year}{mm:02d}"
        url = MONTHLY_ZIP_URL.format(yyyymm=yyyymm)
        resp = requests.head(url, timeout=20)
        if resp.status_code == 200:
            months.append(yyyymm)
        else:
            logger.info("%s: not available (HTTP %d), stopping probe", yyyymm, resp.status_code)
            break
    return months


def backfill_month(yyyymm, db_path=db.DEFAULT_DB_PATH, skip_existing=True):
    """
    Downloads one monthly zip and upserts a daily_spreads row for every
    FINRA_IDS_PXTABLES-YYYYMMDD.xlsx entry inside it.
    Returns (written, skipped, failed) counts.
    """
    import zipfile

    url = MONTHLY_ZIP_URL.format(yyyymm=yyyymm)
    logger.info("Downloading %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    pxtables_names = sorted(n for n in zf.namelist() if PXTABLES_NAME_RE.match(n))
    logger.info("%s: %d PXTABLES files found", yyyymm, len(pxtables_names))

    written = skipped = failed = 0
    for name in pxtables_names:
        filename_date = datetime.strptime(PXTABLES_NAME_RE.match(name).group(1), "%Y%m%d").date()

        if skip_existing and db.get_day(filename_date, db_path=db_path) is not None:
            skipped += 1
            continue

        data = zf.read(name)
        try:
            finra_date = get_data_as_of_date(io.BytesIO(data))
            if finra_date is None:
                raise ValueError("'DATA AS OF:' banner not found in workbook")
            if finra_date != filename_date:
                logger.warning(
                    "%s: internal DATA AS OF (%s) != filename date (%s); using internal date as the row key",
                    name, finra_date, filename_date,
                )

            record = build_daily_record(io.BytesIO(data), finra_date=finra_date)
            # Reference row lookups happen against whatever's already in db_path, so months/days
            # must be (and are) processed in ascending chronological order for QTD to be correct.
            record.update(db.compute_qtd_fields(record, db_path=db_path))
            db.upsert_day(record, db_path=db_path)
            written += 1

            if record["ust_stale"]:
                logger.warning("%s: treasury rate unavailable (no same-day data from either source)", finra_date)
            if record["par_coupon_raw"] is None:
                logger.warning(
                    "%s: raw par coupon not computable (settlement_month=%s, no valid interpolation bracket)",
                    finra_date, record["settlement_month"],
                )
            if record["par_coupon_normalized"] is None:
                logger.warning(
                    "%s: normalized par coupon not computable (near=%s, next=%s)",
                    finra_date, record["settlement_month"], record["next_settlement_month"],
                )
        except Exception:
            logger.exception("%s: failed to process, skipping", name)
            failed += 1

    return written, skipped, failed


def run_backfill(db_path=db.DEFAULT_DB_PATH, months=None, skip_existing=True, year=BACKFILL_YEAR):
    db.init_db(db_path)
    months = months if months is not None else available_months(year)
    logger.info("Backfilling months: %s", months)

    total_written = total_skipped = total_failed = 0
    for yyyymm in months:
        written, skipped, failed = backfill_month(yyyymm, db_path=db_path, skip_existing=skip_existing)
        logger.info("%s done: wrote %d, skipped %d (already in db), failed %d", yyyymm, written, skipped, failed)
        total_written += written
        total_skipped += skipped
        total_failed += failed

    logger.info(
        "Backfill complete: %d written, %d skipped, %d failed across %d month(s)",
        total_written, total_skipped, total_failed, len(months),
    )
    return total_written, total_skipped, total_failed


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("backfill.log")],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-time historical backfill (2026) from FINRA monthly zip archives")
    parser.add_argument("--db-path", default=db.DEFAULT_DB_PATH)
    parser.add_argument("--months", nargs="+", help="explicit yyyymm list, e.g. --months 202601 202602 (default: auto-probe all available 2026 months)")
    parser.add_argument("--no-skip-existing", action="store_true", help="re-process and overwrite days already in the db")
    args = parser.parse_args()

    _setup_logging()

    try:
        run_backfill(db_path=args.db_path, months=args.months, skip_existing=not args.no_skip_existing)
    except Exception:
        logger.exception("Backfill failed")
        sys.exit(1)
