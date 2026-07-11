"""
SQLite storage for daily MBS/Treasury spread data.

One row per trading day (keyed on the FINRA file's trade date), holding the
UST 5yr/10yr rates, the UMBS interpolation bracket (coupon/price on each
side of par), the interpolated par coupon, and the resulting spreads in bps.

spread_5yr  = (par_coupon - ust_5yr)  * 100   # bps
spread_10yr = (par_coupon - ust_10yr) * 100   # bps
spread_avg  = (spread_5yr + spread_10yr) / 2  # bps

Quarter-to-date (QTD) change tracking: each row also stores its change since
the most recent row dated before the first day of its quarter (i.e. the prior
quarter's last available close) - used to estimate rate-driven book value
moves intra-quarter, before official financials post. All QTD deltas are in
bps (including the UST yield changes, for unit consistency with the spreads).
A row in the first quarter a dataset covers has no prior-quarter baseline
available, so its QTD fields are NULL - not a bug, just no reference point.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

DEFAULT_DB_PATH = "mbs_spreads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_spreads (
    finra_date      TEXT PRIMARY KEY,   -- ISO date (YYYY-MM-DD), FINRA file's trade date
    settlement_month TEXT,              -- near-month settlement label used (e.g. "August")
    ust_5yr         REAL,
    ust_10yr        REAL,
    ust_source      TEXT,               -- 'treasury.gov' | 'yahoo_fallback' | NULL
    ust_stale       INTEGER NOT NULL DEFAULT 0,  -- 1 if no same-day treasury rate was available
    coupon_low      REAL,
    price_low       REAL,
    coupon_high     REAL,
    price_high      REAL,
    par_coupon      REAL,
    spread_5yr      REAL,               -- bps
    spread_10yr     REAL,               -- bps
    spread_avg      REAL,               -- bps
    qtd_ref_date    TEXT,               -- date of the prior-quarter reference row used below (NULL if none)
    qtd_chg_ust_5yr      REAL,          -- bps, vs qtd_ref_date
    qtd_chg_ust_10yr     REAL,          -- bps, vs qtd_ref_date
    qtd_chg_spread_5yr   REAL,          -- bps, vs qtd_ref_date
    qtd_chg_spread_10yr  REAL,          -- bps, vs qtd_ref_date
    qtd_chg_spread_avg   REAL,          -- bps, vs qtd_ref_date
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
    if any required input is missing.
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
    are today's value minus the reference row's value, in bps. Any field is
    None if either side of the comparison is missing (no reference row, or
    the metric itself wasn't computable that day).
    """
    ref = get_qtd_reference_row(record["finra_date"], db_path=db_path)
    if ref is None:
        return {
            "qtd_ref_date": None,
            "qtd_chg_ust_5yr": None,
            "qtd_chg_ust_10yr": None,
            "qtd_chg_spread_5yr": None,
            "qtd_chg_spread_10yr": None,
            "qtd_chg_spread_avg": None,
        }

    def _delta_bps(today_val, ref_val, already_bps):
        if today_val is None or ref_val is None:
            return None
        diff = (today_val - ref_val) if already_bps else (today_val - ref_val) * 100
        return round(diff, 2)

    return {
        "qtd_ref_date": ref["finra_date"],
        "qtd_chg_ust_5yr": _delta_bps(record.get("ust_5yr"), ref.get("ust_5yr"), already_bps=False),
        "qtd_chg_ust_10yr": _delta_bps(record.get("ust_10yr"), ref.get("ust_10yr"), already_bps=False),
        "qtd_chg_spread_5yr": _delta_bps(record.get("spread_5yr"), ref.get("spread_5yr"), already_bps=True),
        "qtd_chg_spread_10yr": _delta_bps(record.get("spread_10yr"), ref.get("spread_10yr"), already_bps=True),
        "qtd_chg_spread_avg": _delta_bps(record.get("spread_avg"), ref.get("spread_avg"), already_bps=True),
    }


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

    row = {
        "finra_date": finra_date,
        "settlement_month": record.get("settlement_month"),
        "ust_5yr": record.get("ust_5yr"),
        "ust_10yr": record.get("ust_10yr"),
        "ust_source": record.get("ust_source"),
        "ust_stale": int(bool(record.get("ust_stale", False))),
        "coupon_low": record.get("coupon_low"),
        "price_low": record.get("price_low"),
        "coupon_high": record.get("coupon_high"),
        "price_high": record.get("price_high"),
        "par_coupon": record.get("par_coupon"),
        "spread_5yr": record.get("spread_5yr"),
        "spread_10yr": record.get("spread_10yr"),
        "spread_avg": record.get("spread_avg"),
        "qtd_ref_date": record.get("qtd_ref_date"),
        "qtd_chg_ust_5yr": record.get("qtd_chg_ust_5yr"),
        "qtd_chg_ust_10yr": record.get("qtd_chg_ust_10yr"),
        "qtd_chg_spread_5yr": record.get("qtd_chg_spread_5yr"),
        "qtd_chg_spread_10yr": record.get("qtd_chg_spread_10yr"),
        "qtd_chg_spread_avg": record.get("qtd_chg_spread_avg"),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO daily_spreads (
                finra_date, settlement_month, ust_5yr, ust_10yr, ust_source, ust_stale,
                coupon_low, price_low, coupon_high, price_high, par_coupon,
                spread_5yr, spread_10yr, spread_avg,
                qtd_ref_date, qtd_chg_ust_5yr, qtd_chg_ust_10yr,
                qtd_chg_spread_5yr, qtd_chg_spread_10yr, qtd_chg_spread_avg,
                ingested_at
            ) VALUES (
                :finra_date, :settlement_month, :ust_5yr, :ust_10yr, :ust_source, :ust_stale,
                :coupon_low, :price_low, :coupon_high, :price_high, :par_coupon,
                :spread_5yr, :spread_10yr, :spread_avg,
                :qtd_ref_date, :qtd_chg_ust_5yr, :qtd_chg_ust_10yr,
                :qtd_chg_spread_5yr, :qtd_chg_spread_10yr, :qtd_chg_spread_avg,
                :ingested_at
            )
            ON CONFLICT(finra_date) DO UPDATE SET
                settlement_month=excluded.settlement_month,
                ust_5yr=excluded.ust_5yr,
                ust_10yr=excluded.ust_10yr,
                ust_source=excluded.ust_source,
                ust_stale=excluded.ust_stale,
                coupon_low=excluded.coupon_low,
                price_low=excluded.price_low,
                coupon_high=excluded.coupon_high,
                price_high=excluded.price_high,
                par_coupon=excluded.par_coupon,
                spread_5yr=excluded.spread_5yr,
                spread_10yr=excluded.spread_10yr,
                spread_avg=excluded.spread_avg,
                qtd_ref_date=excluded.qtd_ref_date,
                qtd_chg_ust_5yr=excluded.qtd_chg_ust_5yr,
                qtd_chg_ust_10yr=excluded.qtd_chg_ust_10yr,
                qtd_chg_spread_5yr=excluded.qtd_chg_spread_5yr,
                qtd_chg_spread_10yr=excluded.qtd_chg_spread_10yr,
                qtd_chg_spread_avg=excluded.qtd_chg_spread_avg,
                ingested_at=excluded.ingested_at
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
            "ust_5yr": 3.90,
            "ust_10yr": 4.20,
            "ust_source": "treasury.gov",
            "ust_stale": False,
            "coupon_low": 5.0,
            "price_low": 99.5,
            "coupon_high": 5.5,
            "price_high": 101.2,
            "par_coupon": 5.125,
            "spread_5yr": s5,
            "spread_10yr": s10,
            "spread_avg": savg,
        }
    )
    print(get_day(date(2026, 7, 10)))
    print(get_latest(5))
