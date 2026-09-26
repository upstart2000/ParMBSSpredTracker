"""
SIFMA Class A (30-Year UMBS; pre-6/2019 30-Year Fannie Mae/Freddie Mac) TBA
settlement dates.
Source: https://www.sifma.org/resources/guides-playbooks/mbs-notification-and-settlement-dates
Published ~12 months ahead by SIFMA. Extend this dict as new dates are published
(or replace with a loader that pulls the SIFMA XLSX directly - see note at bottom).

Dec 2017 onward is from SIFMA's current calendar workbook
(SIFMASettlementDatesCalendar-2026-2027.xlsx, which carries history back to
Dec 2017); Jan-Nov 2017 is from SIFMA's own earlier calendar files as
archived on web.archive.org (2016-07-23 and 2017-05-24 snapshots), which
agree with each other and with the current workbook where they overlap.

FINRA's settlement labels are bare month names ("July", "August", ...) with
no year, so they're resolved against the file's trade date via
resolve_settlement_month() - a "January" label in a December file means
next year's January.
"""
from datetime import date
from functools import lru_cache

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# (year, month) -> Class A settlement date
CLASS_A_SETTLEMENT_DATES = {
    # 2017
    (2017, 1): date(2017, 1, 18),
    (2017, 2): date(2017, 2, 13),
    (2017, 3): date(2017, 3, 13),
    (2017, 4): date(2017, 4, 12),
    (2017, 5): date(2017, 5, 11),
    (2017, 6): date(2017, 6, 13),
    (2017, 7): date(2017, 7, 13),
    (2017, 8): date(2017, 8, 14),
    (2017, 9): date(2017, 9, 13),
    (2017, 10): date(2017, 10, 12),
    (2017, 11): date(2017, 11, 13),
    (2017, 12): date(2017, 12, 13),
    # 2018
    (2018, 1): date(2018, 1, 11),
    (2018, 2): date(2018, 2, 13),
    (2018, 3): date(2018, 3, 13),
    (2018, 4): date(2018, 4, 12),
    (2018, 5): date(2018, 5, 14),
    (2018, 6): date(2018, 6, 13),
    (2018, 7): date(2018, 7, 12),
    (2018, 8): date(2018, 8, 13),
    (2018, 9): date(2018, 9, 13),
    (2018, 10): date(2018, 10, 11),
    (2018, 11): date(2018, 11, 13),
    (2018, 12): date(2018, 12, 13),
    # 2019
    (2019, 1): date(2019, 1, 14),
    (2019, 2): date(2019, 2, 13),
    (2019, 3): date(2019, 3, 13),
    (2019, 4): date(2019, 4, 10),
    (2019, 5): date(2019, 5, 13),
    (2019, 6): date(2019, 6, 13),
    (2019, 7): date(2019, 7, 15),
    (2019, 8): date(2019, 8, 13),
    (2019, 9): date(2019, 9, 12),
    (2019, 10): date(2019, 10, 10),
    (2019, 11): date(2019, 11, 13),
    (2019, 12): date(2019, 12, 12),
    # 2020
    (2020, 1): date(2020, 1, 14),
    (2020, 2): date(2020, 2, 12),
    (2020, 3): date(2020, 3, 12),
    (2020, 4): date(2020, 4, 15),
    (2020, 5): date(2020, 5, 13),
    (2020, 6): date(2020, 6, 11),
    (2020, 7): date(2020, 7, 14),
    (2020, 8): date(2020, 8, 13),
    (2020, 9): date(2020, 9, 14),
    (2020, 10): date(2020, 10, 14),
    (2020, 11): date(2020, 11, 12),
    (2020, 12): date(2020, 12, 14),
    # 2021
    (2021, 1): date(2021, 1, 14),
    (2021, 2): date(2021, 2, 11),
    (2021, 3): date(2021, 3, 11),
    (2021, 4): date(2021, 4, 14),
    (2021, 5): date(2021, 5, 13),
    (2021, 6): date(2021, 6, 14),
    (2021, 7): date(2021, 7, 14),
    (2021, 8): date(2021, 8, 12),
    (2021, 9): date(2021, 9, 14),
    (2021, 10): date(2021, 10, 14),
    (2021, 11): date(2021, 11, 10),
    (2021, 12): date(2021, 12, 13),
    # 2022
    (2022, 1): date(2022, 1, 13),
    (2022, 2): date(2022, 2, 14),
    (2022, 3): date(2022, 3, 14),
    (2022, 4): date(2022, 4, 13),
    (2022, 5): date(2022, 5, 12),
    (2022, 6): date(2022, 6, 13),
    (2022, 7): date(2022, 7, 14),
    (2022, 8): date(2022, 8, 11),
    (2022, 9): date(2022, 9, 14),
    (2022, 10): date(2022, 10, 13),
    (2022, 11): date(2022, 11, 14),
    (2022, 12): date(2022, 12, 13),
    # 2023
    (2023, 1): date(2023, 1, 12),
    (2023, 2): date(2023, 2, 13),
    (2023, 3): date(2023, 3, 13),
    (2023, 4): date(2023, 4, 13),
    (2023, 5): date(2023, 5, 11),
    (2023, 6): date(2023, 6, 13),
    (2023, 7): date(2023, 7, 13),
    (2023, 8): date(2023, 8, 14),
    (2023, 9): date(2023, 9, 14),
    (2023, 10): date(2023, 10, 12),
    (2023, 11): date(2023, 11, 13),
    (2023, 12): date(2023, 12, 13),
    # 2024
    (2024, 1): date(2024, 1, 16),
    (2024, 2): date(2024, 2, 13),
    (2024, 3): date(2024, 3, 13),
    (2024, 4): date(2024, 4, 11),
    (2024, 5): date(2024, 5, 13),
    (2024, 6): date(2024, 6, 13),
    (2024, 7): date(2024, 7, 15),
    (2024, 8): date(2024, 8, 13),
    (2024, 9): date(2024, 9, 16),
    (2024, 10): date(2024, 10, 15),
    (2024, 11): date(2024, 11, 14),
    (2024, 12): date(2024, 12, 12),
    # 2025
    (2025, 1): date(2025, 1, 14),
    (2025, 2): date(2025, 2, 13),
    (2025, 3): date(2025, 3, 13),
    (2025, 4): date(2025, 4, 14),
    (2025, 5): date(2025, 5, 13),
    (2025, 6): date(2025, 6, 12),
    (2025, 7): date(2025, 7, 14),
    (2025, 8): date(2025, 8, 13),
    (2025, 9): date(2025, 9, 15),
    (2025, 10): date(2025, 10, 14),
    (2025, 11): date(2025, 11, 13),
    (2025, 12): date(2025, 12, 11),
    # 2026
    (2026, 1): date(2026, 1, 14),
    (2026, 2): date(2026, 2, 12),
    (2026, 3): date(2026, 3, 12),
    (2026, 4): date(2026, 4, 13),
    (2026, 5): date(2026, 5, 13),
    (2026, 6): date(2026, 6, 11),
    (2026, 7): date(2026, 7, 13),
    (2026, 8): date(2026, 8, 13),
    (2026, 9): date(2026, 9, 14),
    (2026, 10): date(2026, 10, 13),
    (2026, 11): date(2026, 11, 12),
    (2026, 12): date(2026, 12, 10),
    # 2027
    (2027, 1): date(2027, 1, 14),
    (2027, 2): date(2027, 2, 11),
    (2027, 3): date(2027, 3, 11),
    (2027, 4): date(2027, 4, 13),
    (2027, 5): date(2027, 5, 13),
    (2027, 6): date(2027, 6, 14),
    (2027, 7): date(2027, 7, 14),
    (2027, 8): date(2027, 8, 12),
    (2027, 9): date(2027, 9, 14),
    (2027, 10): date(2027, 10, 14),
    (2027, 11): date(2027, 11, 15),
    (2027, 12): date(2027, 12, 13),
}


def resolve_settlement_month(month_label, as_of):
    """
    Maps a FINRA settlement label ("January", ...) to a (year, month) key,
    relative to the file's trade date as_of. FINRA quotes the current month
    plus the next few, so the label is taken as the first occurrence of that
    month on/after as_of's month - except the month immediately before
    as_of's, which is treated as the prior month (a front month that hasn't
    dropped off the file yet) rather than 11 months ahead.
    Returns None for an unrecognized label.
    """
    if month_label not in MONTH_NAMES:
        return None
    month = MONTH_NAMES.index(month_label) + 1
    offset = (month - as_of.month) % 12
    if offset == 11:
        offset = -1
    total = as_of.year * 12 + (as_of.month - 1) + offset
    return total // 12, total % 12 + 1


def settlement_date_for(month_label, as_of, settlement_dates=None):
    """Settlement date for a FINRA month label as of trade date as_of, or None if not in the calendar."""
    if settlement_dates is None:
        settlement_dates = CLASS_A_SETTLEMENT_DATES
    key = resolve_settlement_month(month_label, as_of)
    return settlement_dates.get(key) if key else None


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
    years = sorted({d.year for d in CLASS_A_SETTLEMENT_DATES.values()})
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
    settlement_dates: dict (year, month) -> settlement date (defaults to CLASS_A_SETTLEMENT_DATES);
                      FINRA's bare month labels are resolved against `today`

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
        settlement_dates = CLASS_A_SETTLEMENT_DATES

    # Candidate months present in both the calendar and the parsed file, sorted by settlement date.
    candidates = []
    for m in parsed_coupon_data:
        d = settlement_date_for(m, today, settlement_dates)
        if d is not None:
            candidates.append((d, m))
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


def get_next_settlement_month(month_label, as_of, settlement_dates=None):
    """
    Returns the label of the settlement month immediately following
    month_label (as resolved against trade date as_of), within
    settlement_dates (defaults to CLASS_A_SETTLEMENT_DATES). Used by the
    constant-maturity normalization, which interpolates between the near and
    next settlement month's prices. Returns None if month_label can't be
    resolved or the following month isn't covered by the calendar yet.
    """
    if settlement_dates is None:
        settlement_dates = CLASS_A_SETTLEMENT_DATES
    key = resolve_settlement_month(month_label, as_of)
    if key is None or key not in settlement_dates:
        return None
    year, month = key
    next_key = (year + month // 12, month % 12 + 1)
    if next_key not in settlement_dates:
        return None
    return MONTH_NAMES[next_key[1] - 1]


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
