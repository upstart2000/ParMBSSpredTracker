"""
Shared logic to turn a downloaded FINRA_IDS_PXTABLES.xlsx file into one
daily_spreads record (db.py schema). Used by both the nightly job and the
historical backfill so the two stay consistent.
"""
from finra_parser import parse_tba_30y_umbs, compute_par_coupon, get_data_as_of_date
from settlement_calendar import get_near_month_settlement
from treasury_rates import get_treasury_rates
from db import compute_spreads


def build_daily_record(filepath, finra_date=None, allow_yahoo_fallback=True, settlement_dates=None):
    """
    filepath: path to a downloaded FINRA_IDS_PXTABLES.xlsx
    finra_date: the trade date this file's data covers. If None, read from the
                file's own "DATA AS OF:" banner via get_data_as_of_date().
    settlement_dates: optional override dict passed through to
                       get_near_month_settlement (e.g. a historical settlement
                       calendar for backfill; defaults to the 2026 calendar).

    Returns a dict matching db.upsert_day()'s expected keys. Fields that
    couldn't be computed (no bracket, no treasury rate) are left as None /
    ust_stale=True rather than guessed at.
    """
    if finra_date is None:
        finra_date = get_data_as_of_date(filepath)
        if finra_date is None:
            raise ValueError(
                f"Could not determine trade date for {filepath}: "
                "'DATA AS OF:' banner not found and no finra_date was supplied."
            )

    parsed = parse_tba_30y_umbs(filepath)
    month, coupon_prices = get_near_month_settlement(
        parsed, today=finra_date, settlement_dates=settlement_dates
    )

    record = {
        "finra_date": finra_date,
        "settlement_month": month,
        "coupon_low": None,
        "price_low": None,
        "coupon_high": None,
        "price_high": None,
        "par_coupon": None,
    }

    if coupon_prices:
        bracket = compute_par_coupon(coupon_prices)
        if bracket is not None:
            par, (c_low, p_low), (c_high, p_high) = bracket
            record.update(
                par_coupon=par,
                coupon_low=c_low,
                price_low=p_low,
                coupon_high=c_high,
                price_high=p_high,
            )

    treasury = get_treasury_rates(finra_date, allow_yahoo_fallback=allow_yahoo_fallback)
    record["ust_5yr"] = treasury.get("ust_5yr")
    record["ust_10yr"] = treasury.get("ust_10yr")
    record["ust_source"] = treasury.get("source")
    record["ust_stale"] = bool(treasury.get("stale", False))

    spread_5yr, spread_10yr, spread_avg = compute_spreads(
        record["par_coupon"], record["ust_5yr"], record["ust_10yr"]
    )
    record["spread_5yr"] = spread_5yr
    record["spread_10yr"] = spread_10yr
    record["spread_avg"] = spread_avg

    return record
