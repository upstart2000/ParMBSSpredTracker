# ParMBSSpredTracker

Tracks the daily par-coupon spread between 30-year UMBS TBAs and 5yr/10yr US
Treasuries, used to estimate mortgage REIT book value moves intra-quarter.

## How it works

- **`finra_parser.py`** - parses FINRA's `FINRA_IDS_PXTABLES.xlsx` (TBA sheet,
  30yr UMBS; 30yr FNMA before UMBS launched in mid-2019), computes the par
  coupon via linear interpolation between the coupon brackets straddling par.
- **`settlement_calendar.py`** - SIFMA Class A settlement dates (2017-2027);
  picks the near-month TBA settlement bucket.
- **`treasury_rates.py`** - fetches 5yr/10yr UST par yields (Treasury.gov
  primary, Yahoo Finance `^FVX`/`^TNX` fallback).
- **`db.py`** - SQLite storage (`mbs_spreads.db`), one row per trading day:
  rates, UMBS bracket coupons/prices, par coupon, spreads (bps), and
  quarter-to-date (QTD) change vs. the prior quarter's last close.
- **`pipeline.py`** - shared glue tying the above into one daily record.
- **`nightly_job.py`** - downloads the day's FINRA file, fetches rates,
  computes spreads/QTD, upserts into SQLite. Runs on a schedule via
  `.github/workflows/nightly.yml` (GitHub Actions, ~9PM ET + 9:45PM ET retry),
  which commits the updated `mbs_spreads.db` back to this repo.
- **`backfill.py`** - historical backfill from FINRA's monthly zip archives,
  Jan 2017 onward by default (`--start`/`--end` yyyymm to narrow it). Par is
  left blank on days it sits more than half a point past the coupons FINRA
  quotes (mainly Aug 2019 - Feb 2021, plus Sep-Nov 2022, Aug-Nov 2023 and
  Apr 2024 - see `db.KNOWN_DATA_GAPS`).
- **`streamlit_app.py`** - dashboard: daily Prior/Today/Delta table, prominent
  QTD change section, and a historical spread chart with 1M-All range presets
  and a date-range slider.

## Running locally

```
pip install -r requirements.txt
python backfill.py        # one-time, populates history from Jan 2017
python nightly_job.py     # fetch today's row
streamlit run streamlit_app.py
```

## Spread convention

`spread_5yr = (par_coupon - ust_5yr) * 100` (bps), same for `spread_10yr`;
`spread_avg` is the mean of the two. QTD change fields compare today's value
to the most recent row before the current quarter's start, also in bps.
