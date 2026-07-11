"""
Shared logic to turn a downloaded FINRA_IDS_PXTABLES.xlsx file into one
daily_spreads record (db.py schema). Used by both the nightly job and the
historical backfill so the two stay consistent.

Computes two parallel par-coupon/spread series per day:

- "_raw": the near-month-settlement par coupon, as originally built. Using
  "whichever settlement month is nearest today" means the implied price
  naturally drifts as days-to-settlement shrink toward the next roll (the TBA
  "drop" shrinks mechanically as settlement approaches), then jumps when
  rolling to the new near-month - a sawtooth artifact on top of real spread
  movement, unrelated to fundamentals.
- "_normalized": a constant-maturity fix for that artifact. Interpolates
  between the near-month and next-month settlement prices (both already
  quoted per coupon in the FINRA file) to a FIXED target of
  TARGET_DAYS_TO_SETTLEMENT days-to-settlement, instead of the actual
  (variable) days-to-near-month-settlement, then runs the same par-coupon
  bracket interpolation across coupons on those normalized prices.

Both series are computed and stored side by side - the normalized series
does not replace the raw one.
"""
import json

from finra_parser import parse_tba_30y_umbs, compute_par_coupon, get_data_as_of_date
from settlement_calendar import CLASS_A_SETTLEMENT_DATES_2026, get_near_month_settlement, get_next_settlement_month
from treasury_rates import get_treasury_rates
from db import compute_spreads


def _serialize_curve(curve):
    """{coupon: price} -> JSON object with string keys (JSON has no float keys). None if empty/missing."""
    if not curve:
        return None
    return json.dumps({str(c): p for c, p in curve.items()})

TARGET_DAYS_TO_SETTLEMENT = 30  # constant-maturity horizon; adjust here if needed


def _time_interpolate_prices(near_prices, next_prices, days_to_near, days_to_next, target_days):
    """
    Per-coupon-bucket linear interpolation in calendar time (not price-vs-coupon)
    between the near-month and next-month settlement prices, evaluated at
    target_days-to-settlement:

        P_target = P_near + (P_next - P_near) * (target_days - days_to_near) /
                   (days_to_next - days_to_near)

    Only coupons present in both months are usable. Returns {coupon: price},
    empty if days_to_near == days_to_next (degenerate, can't interpolate
    against a zero-width time axis).
    """
    if days_to_next == days_to_near:
        return {}
    normalized = {}
    for coupon, p_near in near_prices.items():
        p_next = next_prices.get(coupon)
        if p_next is None:
            continue
        normalized[coupon] = p_near + (p_next - p_near) * (target_days - days_to_near) / (days_to_next - days_to_near)
    return normalized


def build_normalized_leg(parsed, near_month, finra_date, settlement_dates=None, target_days=TARGET_DAYS_TO_SETTLEMENT):
    """
    Returns the next_settlement_month/days_to_near/days_to_next and
    coupon_low_normalized/price_low_normalized/coupon_high_normalized/
    price_high_normalized/par_coupon_normalized fields. Fields stay None where
    normalization isn't possible (near month not in the settlement calendar,
    no next month available/quoted in this file, or no coupon bucket shared
    between the two months) - that's a real gap, not silently guessed at.
    """
    if settlement_dates is None:
        settlement_dates = CLASS_A_SETTLEMENT_DATES_2026

    result = {
        "next_settlement_month": None,
        "days_to_near": None,
        "days_to_next": None,
        "coupon_low_normalized": None,
        "price_low_normalized": None,
        "coupon_high_normalized": None,
        "price_high_normalized": None,
        "par_coupon_normalized": None,
        "coupon_curve_normalized": {},
    }

    if near_month is None or near_month not in settlement_dates:
        return result

    next_month = get_next_settlement_month(near_month, settlement_dates)
    result["next_settlement_month"] = next_month
    if next_month is None or next_month not in parsed or near_month not in parsed:
        return result

    days_to_near = (settlement_dates[near_month] - finra_date).days
    days_to_next = (settlement_dates[next_month] - finra_date).days
    result["days_to_near"] = days_to_near
    result["days_to_next"] = days_to_next

    normalized_prices = _time_interpolate_prices(
        parsed[near_month], parsed[next_month], days_to_near, days_to_next, target_days
    )
    result["coupon_curve_normalized"] = normalized_prices
    if not normalized_prices:
        return result

    bracket = compute_par_coupon(normalized_prices)
    if bracket is None:
        return result

    par, (c_low, p_low), (c_high, p_high) = bracket
    result.update(
        par_coupon_normalized=par,
        coupon_low_normalized=c_low,
        price_low_normalized=p_low,
        coupon_high_normalized=c_high,
        price_high_normalized=p_high,
    )
    return result


def build_daily_record(filepath, finra_date=None, allow_yahoo_fallback=True, settlement_dates=None):
    """
    filepath: path (or file-like object) to a downloaded FINRA_IDS_PXTABLES.xlsx
    finra_date: the trade date this file's data covers. If None, read from the
                file's own "DATA AS OF:" banner via get_data_as_of_date().
    settlement_dates: optional override dict passed through to
                       get_near_month_settlement / the normalization leg
                       (e.g. a historical settlement calendar; defaults to
                       the 2026 calendar).

    Returns a dict matching db.upsert_day()'s expected keys, with parallel
    _raw and _normalized par-coupon/spread series. Fields that couldn't be
    computed (no bracket, no treasury rate, no normalization inputs) are left
    as None / ust_stale=True rather than guessed at.
    """
    if finra_date is None:
        finra_date = get_data_as_of_date(filepath)
        if finra_date is None:
            raise ValueError(
                f"Could not determine trade date for {filepath}: "
                "'DATA AS OF:' banner not found and no finra_date was supplied."
            )

    parsed = parse_tba_30y_umbs(filepath)
    near_month, coupon_prices = get_near_month_settlement(
        parsed, today=finra_date, settlement_dates=settlement_dates
    )

    record = {
        "finra_date": finra_date,
        "settlement_month": near_month,
        "coupon_low_raw": None,
        "price_low_raw": None,
        "coupon_high_raw": None,
        "price_high_raw": None,
        "par_coupon_raw": None,
    }

    if coupon_prices:
        bracket = compute_par_coupon(coupon_prices)
        if bracket is not None:
            par, (c_low, p_low), (c_high, p_high) = bracket
            record.update(
                par_coupon_raw=par,
                coupon_low_raw=c_low,
                price_low_raw=p_low,
                coupon_high_raw=c_high,
                price_high_raw=p_high,
            )

    record["coupon_curve_raw"] = _serialize_curve(coupon_prices)

    normalized_leg = build_normalized_leg(parsed, near_month, finra_date, settlement_dates=settlement_dates)
    normalized_leg["coupon_curve_normalized"] = _serialize_curve(normalized_leg["coupon_curve_normalized"])
    record.update(normalized_leg)

    treasury = get_treasury_rates(finra_date, allow_yahoo_fallback=allow_yahoo_fallback)
    record["ust_5yr"] = treasury.get("ust_5yr")
    record["ust_10yr"] = treasury.get("ust_10yr")
    record["ust_source"] = treasury.get("source")
    record["ust_stale"] = bool(treasury.get("stale", False))

    spread_5yr_raw, spread_10yr_raw, spread_avg_raw = compute_spreads(
        record["par_coupon_raw"], record["ust_5yr"], record["ust_10yr"]
    )
    record["spread_5yr_raw"] = spread_5yr_raw
    record["spread_10yr_raw"] = spread_10yr_raw
    record["spread_avg_raw"] = spread_avg_raw

    spread_5yr_norm, spread_10yr_norm, spread_avg_norm = compute_spreads(
        record["par_coupon_normalized"], record["ust_5yr"], record["ust_10yr"]
    )
    record["spread_5yr_normalized"] = spread_5yr_norm
    record["spread_10yr_normalized"] = spread_10yr_norm
    record["spread_avg_normalized"] = spread_avg_norm

    return record
