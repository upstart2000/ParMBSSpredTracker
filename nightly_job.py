"""
Nightly job: download FINRA_IDS_PXTABLES.xlsx, parse it, fetch same-day Treasury
rates, compute spreads and QTD changes, and upsert one row into SQLite.

Runs on GitHub Actions (see .github/workflows/nightly.yml), which schedules
this to fire around 9PM ET with a 9:45PM ET retry (both scheduled separately
at the workflow level, across the EDT/EST UTC offsets - see that file for the
cron design). Because of that, this script no longer tries to wait out the
whole release window in-process:

- fetch_finra_file_with_retry()'s retry/backoff here only needs to absorb a
  transient network blip within one run (a couple of quick retries) - it is
  NOT responsible for waiting out FINRA's release window anymore. That's the
  job of the separate 9PM / 9:45PM scheduled firings.
- run_nightly_job() checks for an already-complete row for the expected date
  before doing any work, and exits immediately if found. This makes the
  9:45PM retry (and the extra EDT/EST-duplicate firings) a fast no-op once
  the 9PM run has already succeeded, rather than a wasted re-download.

fetch_finra_file_with_retry() re-downloads until the file's own "DATA AS OF:"
stamp catches up to the expected trade date (today, in America/New_York),
rather than treating a stale-but-200 response as success.

Treasury rate staleness: get_treasury_rates() already refuses to substitute a
prior day's rate - it returns {'stale': True, ust_5yr: None, ...} if neither
treasury.gov nor the Yahoo fallback has same-day data. This job preserves that:
a stale rate is written as NULL with ust_stale=1 and logged as a warning, never
silently backfilled with an old value. Because a stale row is never marked
"complete" (see _row_is_complete), a later run (the 9:45PM retry, or a manual
rerun) will retry it rather than skipping.
"""
import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import db
from finra_parser import get_data_as_of_date
from pipeline import build_daily_record

FINRA_URL = "https://cdn.finra.org/trace/FINRA_IDS_PXTABLES.xlsx"
DOWNLOAD_DIR = "downloads"
EASTERN = ZoneInfo("America/New_York")

# Small/quick by design: this only needs to survive a transient network blip
# within a single CI run. Waiting out FINRA's release window is handled by
# the workflow's separate 9PM / 9:45PM ET scheduled firings, not by sleeping
# here (a GH Actions job sleeping for tens of minutes burns billed minutes).
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 20
DEFAULT_BACKOFF_MULTIPLIER = 2
DEFAULT_MAX_BACKOFF_SEC = 120

logger = logging.getLogger("nightly_job")


def expected_trade_date(now=None):
    """
    The trading day whose data we expect FINRA to release this evening.
    Assumes the job runs the same evening as the trading day (~9PM ET, with a
    9:45PM ET retry - see .github/workflows/nightly.yml), and rolls a weekend
    date back to the prior weekday. Does NOT account for market holidays -
    see is_sifma_holiday(), checked separately in run_nightly_job() so a
    holiday is treated as an expected no-data day, not an error.
    """
    if now is None:
        now = datetime.now(EASTERN)
    d = now.date()
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    return d


def is_sifma_holiday(d):
    """
    True if d is not a SIFMA US bond market trading day (holiday or weekend).
    Uses the SIFMA calendar specifically, not the NYSE equity calendar - they
    differ on days like Columbus Day and Veterans Day (bond market closed,
    equity market open).
    """
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar("SIFMA_US")
    schedule = cal.schedule(start_date=d, end_date=d)
    return schedule.empty


def fetch_finra_file_with_retry(
    url=FINRA_URL,
    expected_date=None,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    initial_backoff_sec=DEFAULT_INITIAL_BACKOFF_SEC,
    backoff_multiplier=DEFAULT_BACKOFF_MULTIPLIER,
    max_backoff_sec=DEFAULT_MAX_BACKOFF_SEC,
    save_dir=DOWNLOAD_DIR,
):
    """
    Downloads `url`, retrying with exponential backoff until the file's
    "DATA AS OF:" date is >= expected_date, or max_attempts is exhausted.
    Saves the accepted file as FINRA_IDS_PXTABLES_<data_as_of>.xlsx in save_dir.
    Returns (filepath, data_as_of_date). Raises RuntimeError if never fresh.
    """
    if expected_date is None:
        expected_date = expected_trade_date()

    os.makedirs(save_dir, exist_ok=True)
    tmp_path = os.path.join(save_dir, "_tmp_download.xlsx")
    backoff = initial_backoff_sec
    last_data_as_of = None
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            last_error = e
            logger.warning("attempt %d/%d: download failed: %s", attempt, max_attempts, e)
        else:
            with open(tmp_path, "wb") as f:
                f.write(resp.content)
            try:
                data_as_of = get_data_as_of_date(tmp_path)
            except Exception as e:
                last_error = e
                data_as_of = None
                logger.warning("attempt %d/%d: downloaded but failed to parse: %s", attempt, max_attempts, e)
            last_data_as_of = data_as_of

            if data_as_of is not None and data_as_of >= expected_date:
                final_path = os.path.join(save_dir, f"FINRA_IDS_PXTABLES_{data_as_of.isoformat()}.xlsx")
                os.replace(tmp_path, final_path)
                logger.info("attempt %d/%d: fresh file received, DATA AS OF %s", attempt, max_attempts, data_as_of)
                return final_path, data_as_of

            if data_as_of is not None:
                logger.info(
                    "attempt %d/%d: file not yet refreshed (DATA AS OF %s, expected >= %s)",
                    attempt, max_attempts, data_as_of, expected_date,
                )

        if attempt < max_attempts:
            logger.info("sleeping %ds before retry", backoff)
            time.sleep(backoff)
            backoff = min(backoff * backoff_multiplier, max_backoff_sec)

    raise RuntimeError(
        f"FINRA file never reached expected trade date {expected_date} after "
        f"{max_attempts} attempts (last DATA AS OF seen: {last_data_as_of}; "
        f"last error: {last_error})"
    )


def _row_is_complete(row):
    """A row only counts as 'done' if it has both a raw par coupon and a fresh (non-stale) treasury rate."""
    return row is not None and not row["ust_stale"] and row["par_coupon_raw"] is not None


def run_nightly_job(
    db_path=db.DEFAULT_DB_PATH,
    finra_url=FINRA_URL,
    expected_date=None,
    allow_yahoo_fallback=True,
    save_dir=DOWNLOAD_DIR,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    initial_backoff_sec=DEFAULT_INITIAL_BACKOFF_SEC,
    force=False,
):
    expected_date = expected_date or expected_trade_date()
    logger.info("Nightly job starting, expected trade date %s", expected_date)

    if is_sifma_holiday(expected_date):
        logger.info(
            "%s is a SIFMA bond market holiday - no data expected, exiting cleanly (no fetch, no retry).",
            expected_date,
        )
        return {"finra_date": expected_date, "sifma_holiday": True}

    db.init_db(db_path)

    latest = db.get_latest(1, db_path=db_path)
    if latest and latest[0]["finra_date"] != expected_date.isoformat():
        gap_days = (expected_date - date.fromisoformat(latest[0]["finra_date"])).days
        if gap_days > db.MAX_EXPECTED_GAP_DAYS:
            logger.warning(
                "Data gap: most recent row before this run is %s, %d calendar days before "
                "expected trade date %s - wider than a normal weekend/holiday. Likely missing "
                "trading day(s); may need a backfill once the source data is available.",
                latest[0]["finra_date"], gap_days, expected_date,
            )

    if not force:
        existing = db.get_day(expected_date, db_path=db_path)
        if _row_is_complete(existing):
            logger.info(
                "Row for %s already complete (par_coupon_raw=%s, stale=%s) - skipping. Pass --force to reprocess.",
                expected_date, existing["par_coupon_raw"], existing["ust_stale"],
            )
            return existing

    filepath, data_as_of = fetch_finra_file_with_retry(
        url=finra_url,
        expected_date=expected_date,
        max_attempts=max_attempts,
        initial_backoff_sec=initial_backoff_sec,
        save_dir=save_dir,
    )

    record = build_daily_record(filepath, finra_date=data_as_of, allow_yahoo_fallback=allow_yahoo_fallback)
    record.update(db.compute_qtd_fields(record, db_path=db_path))

    if record["ust_stale"]:
        logger.warning(
            "Treasury rate STALE for %s: no same-day rate from treasury.gov or Yahoo fallback. "
            "ust_5yr/ust_10yr/spreads written as NULL - re-run this job later to fill them in.",
            data_as_of,
        )
    if record["par_coupon_raw"] is None:
        logger.warning(
            "Raw par coupon not computable for %s (settlement_month=%s): no valid interpolation bracket.",
            data_as_of, record["settlement_month"],
        )
    if record["par_coupon_normalized"] is None:
        logger.warning(
            "Normalized par coupon not computable for %s (near=%s, next=%s): "
            "missing next-month data or no shared coupon bucket.",
            data_as_of, record["settlement_month"], record["next_settlement_month"],
        )

    db.upsert_day(record, db_path=db_path)

    logger.info(
        "Wrote %s: settlement=%s par_coupon_raw=%s par_coupon_normalized=%s ust_5yr=%s ust_10yr=%s "
        "spread_avg_raw=%s spread_avg_normalized=%s stale=%s qtd_ref=%s qtd_chg_spread_avg_raw=%s",
        data_as_of, record["settlement_month"], record["par_coupon_raw"], record["par_coupon_normalized"],
        record["ust_5yr"], record["ust_10yr"], record["spread_avg_raw"], record["spread_avg_normalized"],
        record["ust_stale"], record["qtd_ref_date"], record["qtd_chg_spread_avg_raw"],
    )
    return record


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("nightly_job.log"),
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nightly FINRA/Treasury spread ingestion job")
    parser.add_argument("--date", help="override expected trade date (YYYY-MM-DD); default: today in America/New_York")
    parser.add_argument("--db-path", default=db.DEFAULT_DB_PATH)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--initial-backoff", type=int, default=DEFAULT_INITIAL_BACKOFF_SEC, help="seconds")
    parser.add_argument("--no-yahoo-fallback", action="store_true")
    parser.add_argument("--force", action="store_true", help="reprocess even if a complete row already exists for the expected date")
    args = parser.parse_args()

    _setup_logging()

    expected = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    try:
        run_nightly_job(
            db_path=args.db_path,
            expected_date=expected,
            allow_yahoo_fallback=not args.no_yahoo_fallback,
            max_attempts=args.max_attempts,
            initial_backoff_sec=args.initial_backoff,
            force=args.force,
        )
    except Exception:
        logger.exception("Nightly job failed")
        sys.exit(1)
