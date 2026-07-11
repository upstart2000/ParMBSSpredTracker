"""
SQLite storage for daily MBS/Treasury spread data.

One row per trading day (keyed on the FINRA file's trade date), holding the
UST 5yr/10yr rates and two parallel par-coupon/spread series:

- "_raw": uses whichever settlement month is nearest today (as originally
  built). This naturally drifts as days-to-settlement shrink toward the next
  roll, then jumps at the roll - a sawtooth artifact layered on top of real
  spread movement.
- "_normalized": interpolates between the near-month and next-month prices
  (per coupon bucket) to a fixed TARGET_DAYS_TO_SETTLEMENT-day horizon (see
  pipeline.py), removing that artifact. This is an addition alongside the
  raw series, not a replacement.

spread_5yr_raw  = (par_coupon_raw - ust_5yr)  * 100   # bps
spread_10yr_raw = (par_coupon_raw - ust_10yr) * 100   # bps
spread_avg_raw  = (spread_5yr_raw + spread_10yr_raw) / 2
(same formulas for the _normalized columns, using par_coupon_normalized)

Quarter-to-date (QTD) change tracking: each row also stores its change since
the most recent row dated before the first day of its quarter (i.e. the prior
quarter's last available close) - used to estimate rate-driven book value
moves intra-quarter, before official financials post. All QTD deltas are in
bps (including the UST yield changes, for unit consistency with the spreads).
QTD is tracked for both the raw and normalized spread series. A row in the
first quarter a dataset covers has no prior-quarter baseline available, so
its QTD fields are NULL - not a bug, just no reference point.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

DEFAULT_DB_PATH = "mbs_spreads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_spreads (
    finra_date      TEXT PRIMARY KEY,   -- ISO date (YYYY-MM-DD), FINRA file's trade date
    settlement_month TEXT,              -- near-month settlement label (used for the _raw series)
    next_settlement_month TEXT,         -- next settlement month (the normalization's "next" leg)
    days_to_near    INTEGER,            -- calendar days from finra_date to near-month settlement
    days_to_next    INTEGER,            -- calendar days from finra_date to next-month settlement
    ust_5yr         REAL,
    ust_10yr        REAL,
    ust_source      TEXT,               -- 'treasury.gov' | 'yahoo_fallback' | NULL
    ust_stale       INTEGER NOT NULL DEFAULT 0,  -- 1 if no same-day treasury rate was available

    coupon_low_raw      REAL,
    price_low_raw       REAL,
    coupon_high_raw     REAL,
    price_high_raw      REAL,
    par_coupon_raw      REAL,
    spread_5yr_raw      REAL,           -- bps
    spread_10yr_raw     REAL,           -- bps
    spread_avg_raw      REAL,           -- bps

    coupon_low_normalized      REAL,
    price_low_normalized       REAL,
    coupon_high_normalized     REAL,
    price_high_normalized      REAL,
    par_coupon_normalized      REAL,
    spread_5yr_normalized      REAL,    -- bps
    spread_10yr_normalized     REAL,    -- bps
    spread_avg_normalized      REAL,    -- bps

    qtd_ref_date    TEXT,               -- date of the prior-quarter reference row used below (NULL if none)
    qtd_chg_ust_5yr                 REAL,  -- bps, vs qtd_ref_date
    qtd_chg_ust_10yr                REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_5yr_raw          REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_10yr_raw         REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_avg_raw          REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_5yr_normalized   REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_10yr_normalized  REAL,  -- bps, vs qtd_ref_date
    qtd_chg_spread_avg_normalized   REAL,  -- bps, vs qtd_ref_date

    ingested_at     TEXT NOT NULL       -- UTC timestamp this row was last written
);
"""


@contextmanager
def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path=DEFAULT_DB_PATH):
    with _connect(db_path) as conn:
        conn.execute(SCHEMA)


def compute_spreads(par_coupon, ust_5yr, ust_10yr):
    """
    Returns (spread_5yr, spread_10yr, spread_avg) in bps, or (None, None, None)
    if any required input is missing. Generic over which par coupon series
    (raw or normalized) is passed in - callers assign the results to the
    appropriately-suffixed fields.
    """
    if par_coupon is None or ust_5yr is None or ust_10yr is None:
        return None, None, None
    spread_5yr = round((par_coupon - ust_5yr) * 100, 2)
    spread_10yr = round((par_coupon - ust_10yr) * 100, 2)
    spread_avg = round((spread_5yr + spread_10yr) / 2, 2)
    return spread_5yr, spread_10yr, spread_avg


def get_quarter_start(d):
    """First calendar day of the quarter containing date d."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    quarter_start_month = 3 * ((d.month - 1) // 3) + 1
    return date(d.year, quarter_start_month, 1)


def get_qtd_reference_row(finra_date, db_path=DEFAULT_DB_PATH):
    """
    The most recent row dated strictly before finra_date's quarter start -
    i.e. the prior quarter's last available close. Returns None if no such
    row exists in the db (e.g. the very first quarter this dataset covers).
    """
    quarter_start = get_quarter_start(finra_date)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM daily_spreads WHERE finra_date < ? ORDER BY finra_date DESC LIMIT 1",
            (quarter_start.isoformat(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def compute_qtd_fields(record, db_path=DEFAULT_DB_PATH):
    """
    Given a record dict (as built by pipeline.build_daily_record, pre-upsert),
    returns the qtd_ref_date / qtd_chg_* fields to merge into it. All deltas
    are today's value minus the reference row's value, in bps. Tracks both
    the raw and normalized spread series. Any field is None if either side of
    the comparison is missing (no reference row, or the metric itself wasn't
    computable that day).
    """
    empty = {
        "qtd_ref_date": None,
        "qtd_chg_ust_5yr": None,
        "qtd_chg_ust_10yr": None,
        "qtd_chg_spread_5yr_raw": None,
        "qtd_chg_spread_10yr_raw": None,
        "qtd_chg_spread_avg_raw": None,
        "qtd_chg_spread_5yr_normalized": None,
        "qtd_chg_spread_10yr_normalized": None,
        "qtd_chg_spread_avg_normalized": None,
    }

    ref = get_qtd_reference_row(record["finra_date"], db_path=db_path)
    if ref is None:
        return empty

    def _delta_bps(today_val, ref_val, already_bps):
        if today_val is None or ref_val is None:
            return None
        diff = (today_val - ref_val) if already_bps else (today_val - ref_val) * 100
        return round(diff, 2)

    return {
        "qtd_ref_date": ref["finra_date"],
        "qtd_chg_ust_5yr": _delta_bps(record.get("ust_5yr"), ref.get("ust_5yr"), already_bps=False),
        "qtd_chg_ust_10yr": _delta_bps(record.get("ust_10yr"), ref.get("ust_10yr"), already_bps=False),
        "qtd_chg_spread_5yr_raw": _delta_bps(record.get("spread_5yr_raw"), ref.get("spread_5yr_raw"), already_bps=True),
        "qtd_chg_spread_10yr_raw": _delta_bps(record.get("spread_10yr_raw"), ref.get("spread_10yr_raw"), already_bps=True),
        "qtd_chg_spread_avg_raw": _delta_bps(record.get("spread_avg_raw"), ref.get("spread_avg_raw"), already_bps=True),
        "qtd_chg_spread_5yr_normalized": _delta_bps(
            record.get("spread_5yr_normalized"), ref.get("spread_5yr_normalized"), already_bps=True
        ),
        "qtd_chg_spread_10yr_normalized": _delta_bps(
            record.get("spread_10yr_normalized"), ref.get("spread_10yr_normalized"), already_bps=True
        ),
        "qtd_chg_spread_avg_normalized": _delta_bps(
            record.get("spread_avg_normalized"), ref.get("spread_avg_normalized"), already_bps=True
        ),
    }


_COLUMNS = [
    "finra_date", "settlement_month", "next_settlement_month", "days_to_near", "days_to_next",
    "ust_5yr", "ust_10yr", "ust_source", "ust_stale",
    "coupon_low_raw", "price_low_raw", "coupon_high_raw", "price_high_raw", "par_coupon_raw",
    "spread_5yr_raw", "spread_10yr_raw", "spread_avg_raw",
    "coupon_low_normalized", "price_low_normalized", "coupon_high_normalized", "price_high_normalized",
    "par_coupon_normalized", "spread_5yr_normalized", "spread_10yr_normalized", "spread_avg_normalized",
    "qtd_ref_date", "qtd_chg_ust_5yr", "qtd_chg_ust_10yr",
    "qtd_chg_spread_5yr_raw", "qtd_chg_spread_10yr_raw", "qtd_chg_spread_avg_raw",
    "qtd_chg_spread_5yr_normalized", "qtd_chg_spread_10yr_normalized", "qtd_chg_spread_avg_normalized",
    "ingested_at",
]


def upsert_day(record, db_path=DEFAULT_DB_PATH):
    """
    record: dict with keys matching the daily_spreads columns (finra_date required;
    all others optional / may be None). ingested_at is stamped automatically.
    Insert-or-replace keyed on finra_date, so re-running the nightly job or backfill
    for the same day is idempotent.
    """
    finra_date = record["finra_date"]
    if isinstance(finra_date, date):
        finra_date = finra_date.isoformat()

    row = {col: record.get(col) for col in _COLUMNS}
    row["finra_date"] = finra_date
    row["ust_stale"] = int(bool(record.get("ust_stale", False)))
    row["ingested_at"] = datetime.now(timezone.utc).isoformat()

    placeholders = ", ".join(f":{c}" for c in _COLUMNS)
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "finra_date")

    with _connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO daily_spreads ({", ".join(_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(finra_date) DO UPDATE SET {update_clause}
            """,
            row,
        )


def get_day(finra_date, db_path=DEFAULT_DB_PATH):
    if isinstance(finra_date, date):
        finra_date = finra_date.isoformat()
    with _connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM daily_spreads WHERE finra_date = ?", (finra_date,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_latest(n=2, db_path=DEFAULT_DB_PATH):
    """Most recent n rows, ordered ascending by date (oldest first)."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM daily_spreads ORDER BY finra_date DESC LIMIT ?", (n,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        return list(reversed(rows))


MAX_EXPECTED_GAP_DAYS = 4  # a normal weekend is 3; a Monday/Friday holiday + weekend is 4


def find_date_gaps(db_path=DEFAULT_DB_PATH, max_expected_gap_days=MAX_EXPECTED_GAP_DAYS):
    """
    Scans all stored dates in order and returns a list of
    (date_before, date_after, calendar_gap_days) for every consecutive pair
    whose gap exceeds max_expected_gap_days - i.e. wider than an ordinary
    weekend or a single holiday long-weekend, and therefore likely missing
    trading day(s) rather than an expected non-trading stretch. We don't have
    a full market holiday calendar wired in (only SIFMA settlement dates), so
    this is a heuristic threshold, not exact - but a 4-day cap catches
    anything wider than one holiday weekend.
    """
    with _connect(db_path) as conn:
        cur = conn.execute("SELECT finra_date FROM daily_spreads ORDER BY finra_date ASC")
        dates = [date.fromisoformat(r["finra_date"]) for r in cur.fetchall()]

    gaps = []
    for prev_date, next_date in zip(dates, dates[1:]):
        gap_days = (next_date - prev_date).days
        if gap_days > max_expected_gap_days:
            gaps.append((prev_date, next_date, gap_days))
    return gaps


def get_all(db_path=DEFAULT_DB_PATH):
    """All rows ordered ascending by date - primarily for the historical chart."""
    with _connect(db_path) as conn:
        cur = conn.execute("SELECT * FROM daily_spreads ORDER BY finra_date ASC")
        return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    # Smoke test: init, upsert a row, read it back.
    init_db(DEFAULT_DB_PATH)
    s5, s10, savg = compute_spreads(5.125, 3.90, 4.20)
    upsert_day(
        {
            "finra_date": date(2026, 7, 10),
            "settlement_month": "August",
            "next_settlement_month": "September",
            "days_to_near": 34,
            "days_to_next": 65,
            "ust_5yr": 3.90,
            "ust_10yr": 4.20,
            "ust_source": "treasury.gov",
            "ust_stale": False,
            "coupon_low_raw": 5.0,
            "price_low_raw": 99.5,
            "coupon_high_raw": 5.5,
            "price_high_raw": 101.2,
            "par_coupon_raw": 5.125,
            "spread_5yr_raw": s5,
            "spread_10yr_raw": s10,
            "spread_avg_raw": savg,
        }
    )
    print(get_day(date(2026, 7, 10)))
    print(get_latest(5))
