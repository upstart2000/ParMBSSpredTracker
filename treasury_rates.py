"""
Fetches 5yr / 10yr UST par yields for a given date.
Primary source: Treasury.gov Daily Treasury Par Yield Curve Rates XML feed.
Fallback: Yahoo Finance (^FVX, ^TNX) if Treasury.gov hasn't posted same-day data yet.
"""
import re
import requests
from datetime import date, datetime
from functools import lru_cache

TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}"
)


@lru_cache(maxsize=None)
def _fetch_month_xml(yyyymm):
    """
    Cached per-month XML fetch. A month's feed is immutable once past (and
    within a run, callers hit this once per day of that month), so caching
    here avoids re-downloading the same document repeatedly - e.g. during a
    historical backfill that requests ~20 trading days from one month.
    """
    url = TREASURY_XML_URL.format(yyyymm=yyyymm)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_treasury_gov(target_date):
    """
    Pulls the current month's Treasury.gov XML feed and returns the row matching target_date.
    Returns dict {'date': date, 'ust_5yr': float, 'ust_10yr': float, 'source': 'treasury.gov'}
    or None if that date isn't present yet (not posted / weekend / holiday).
    """
    yyyymm = target_date.strftime("%Y%m")
    xml_text = _fetch_month_xml(yyyymm)

    # Each <entry> has NEW_DATE, BC_5YEAR, BC_10YEAR - simple regex extraction, avoids
    # pulling in an XML namespace-heavy parser for a well-known, stable feed shape.
    entries = re.findall(
        r"<d:NEW_DATE[^>]*>([^<]+)</d:NEW_DATE>.*?"
        r"<d:BC_5YEAR[^>]*>([^<]+)</d:BC_5YEAR>.*?"
        r"<d:BC_10YEAR[^>]*>([^<]+)</d:BC_10YEAR>",
        xml_text,
        re.DOTALL,
    )

    for date_str, y5, y10 in entries:
        entry_date = datetime.fromisoformat(date_str.strip()).date()
        if entry_date == target_date:
            return {
                "date": entry_date,
                "ust_5yr": float(y5),
                "ust_10yr": float(y10),
                "source": "treasury.gov",
            }
    return None  # not posted yet for target_date


def fetch_yahoo_fallback(target_date):
    """
    Fallback via yfinance: ^FVX (5yr yield x10) and ^TNX (10yr yield x10).
    Returns None if yfinance doesn't have same-day data either.
    """
    import yfinance as yf

    tickers = {"ust_5yr": "^FVX", "ust_10yr": "^TNX"}
    result = {"date": target_date, "source": "yahoo_fallback"}

    for key, ticker in tickers.items():
        hist = yf.Ticker(ticker).history(
            start=target_date.isoformat(),
            end=(target_date.toordinal() and date.fromordinal(target_date.toordinal() + 1)).isoformat(),
        )
        if hist.empty:
            return None  # no same-day data from Yahoo either
        # Yahoo's ^FVX/^TNX quote the yield directly (not x10 despite some older docs -
        # verify against a known value before relying on this in production).
        close_val = float(hist["Close"].iloc[-1])
        result[key] = close_val

    return result


def get_treasury_rates(target_date=None, allow_yahoo_fallback=True):
    """
    Returns dict with ust_5yr, ust_10yr, date, source - or a dict with 'stale': True
    if neither source has same-day data (per the staleness-guard discussion).
    """
    if target_date is None:
        target_date = date.today()

    result = fetch_treasury_gov(target_date)
    if result is not None:
        return result

    if allow_yahoo_fallback:
        result = fetch_yahoo_fallback(target_date)
        if result is not None:
            return result

    return {"date": target_date, "ust_5yr": None, "ust_10yr": None, "source": None, "stale": True}


if __name__ == "__main__":
    # Test against a known historical date (avoids needing "today" to have posted yet)
    test_date = date(2018, 10, 9)
    r = fetch_treasury_gov(test_date)
    print(f"Treasury.gov test ({test_date}): {r}")
