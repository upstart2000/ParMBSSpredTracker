"""
SIFMA Class A (30-Year UMBS) TBA settlement dates.
Source: https://www.sifma.org/resources/guides-playbooks/mbs-notification-and-settlement-dates
Published ~12 months ahead by SIFMA. Extend this dict as new dates are published
(or replace with a loader that pulls the SIFMA XLSX directly - see note at bottom).
"""
from datetime import date
from functools import lru_cache

# month_label matches the FINRA file's settlement labels ("July", "August", ...)
CLASS_A_SETTLEMENT_DATES_2026 = {
    "January":   date(2026, 1, 14),
    "February":  date(2026, 2, 12),
    "March":     date(2026, 3, 12),
    "April":     date(2026, 4, 13),
    "May":       date(2026, 5, 13),
    "June":      date(2026, 6, 11),
    "July":      date(2026, 7, 13),
    "August":    date(2026, 8, 13),
    "September": date(2026, 9, 14),
    "October":   date(2026, 10, 13),
    "November":  date(2026, 11, 12),
    "December":  date(2026, 12, 10),
}

# MBSCC "good delivery" notification for a Class A pass-through is 2 SIFMA
# business days before settlement (the "48-hour rule"). Desks roll out of the
# expiring month ahead of notification, not on it, so by convention the front
# month should stop being treated as "near" one business day earlier still -
# i.e. 3 SIFMA business days before settlement. Confirmed against real data:
# September settles 2026-09-14 (notification 2026-09-10); FINRA's September
# TBA column was already thin/unusable by 2026-09-14 because desks had rolled
# to October days earlier, and get_near_month_settlement() was still trying
# September through then instead of switching on 2026-09-09.
ROLL_BUSINESS_DAYS_BEFORE_SETTLEMENT = 3


@lru_cache(maxsize=1)
def _sifma_trading_days():
    """
    Cached sorted tuple of SIFMA_US trading days spanning (and padded a month
    on either side of) the settlement calendar's covered range. Used to count
    real bond-market business days back from a settlement date for
    roll_date() - a plain Mon-Fri skip would get the wrong answer whenever the
    lookback window crosses a bond holiday that isn't an equity holiday (e.g.
    Columbus Day, Veterans Day - see the similar note in nightly_job.py's
    is_sifma_holiday()).
    """
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar("SIFMA_US")
    years = sorted({d.year for d in CLASS_A_SETTLEMENT_DATES_2026.values()})
    start = date(years[0] - 1, 12, 1)
    end = date(years[-1] + 1, 1, 31)
    schedule = cal.schedule(start_date=start, end_date=end)
    return tuple(ts.date() for ts in schedule.index)


def roll_date(settlement_date, business_days_before=ROLL_BUSINESS_DAYS_BEFORE_SETTLEMENT):
    """
    The date on/after which a month's TBA contract should no longer be
    selected as the "near" month for pricing - business_days_before SIFMA
    trading days before its settlement date.
    """
    trading_days = _sifma_trading_days()
    idx = trading_days.index(settlement_date)
    return trading_days[idx - business_days_before]


def get_near_month_settlement(parsed_coupon_data, today=None, settlement_dates=None):
    """
    parsed_coupon_data: dict from finra_parser.parse_tba_30y_umbs()
                         { month_label: {coupon: price} }
    today: date to evaluate from (defaults to today)
    settlement_dates: dict month_label -> settlement date (defaults to CLASS_A_SETTLEMENT_DATES_2026)

    Selection rule:
      1. Start with the earliest settlement month that hasn't rolled off yet -
         i.e. today is still before that month's roll_date() (3 SIFMA business
         days ahead of its settlement, ahead of the ~48-hour notification
         date desks actually roll on). This is deliberately *not* "settlement
         date >= today": by the time a month actually settles, real trading
         has already moved to the next month days earlier, and the FINRA
         column for the settling month is typically too thin/rolled-off to
         price against.
      2. If that month's data doesn't yield a computable par coupon (thin/rolled-off data),
         fall through to the next month in the FINRA file, in order.
      3. Returns (month_label, coupon_prices) or (None, None) if nothing usable is found.
    """
    from finra_parser import compute_par_coupon

    if today is None:
        today = date.today()
    if settlement_dates is None:
        settlement_dates = CLASS_A_SETTLEMENT_DATES_2026

    # Candidate months present in both the calendar and the parsed file, sorted by settlement date.
    candidates = [
        (settlement_dates[m], m) for m in parsed_coupon_data if m in settlement_dates
    ]
    candidates.sort()

    # Start from the earliest month that hasn't rolled off yet (see roll_date()).
    not_yet_rolled = [m for d, m in candidates if today < roll_date(d)]
    # Fall back to file order if calendar coverage is short (e.g. testing on stale data).
    ordered_months = not_yet_rolled if not_yet_rolled else [m for _, m in candidates]

    for month in ordered_months:
        coupon_prices = parsed_coupon_data[month]
        result = compute_par_coupon(coupon_prices)
        if result is not None:
            return month, coupon_prices

    return None, None


def get_next_settlement_month(month_label, settlement_dates=None):
    """
    Returns the month label whose settlement date immediately follows
    month_label's, within settlement_dates (defaults to
    CLASS_A_SETTLEMENT_DATES_2026). Used by the constant-maturity
    normalization, which interpolates between the near and next settlement
    month's prices. Returns None if month_label isn't in settlement_dates or
    is the last one covered (e.g. December, with no 2027 calendar yet).
    """
    if settlement_dates is None:
        settlement_dates = CLASS_A_SETTLEMENT_DATES_2026
    if month_label not in settlement_dates:
        return None
    ordered_labels = [m for m, _ in sorted(settlement_dates.items(), key=lambda kv: kv[1])]
    idx = ordered_labels.index(month_label)
    if idx + 1 >= len(ordered_labels):
        return None
    return ordered_labels[idx + 1]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/claude/mbs_tracker")
    from finra_parser import parse_tba_30y_umbs, compute_par_coupon

    filepath = "/mnt/user-data/uploads/FINRA_IDS_PXTABLES.xlsx"
    parsed = parse_tba_30y_umbs(filepath)

    month, coupon_prices = get_near_month_settlement(parsed, today=date(2026, 7, 11))
    print(f"Near-month settlement selected: {month}")
    par, low, high = compute_par_coupon(coupon_prices)
    print(f"Par coupon: {par}%  (bracket {low} / {high})")
