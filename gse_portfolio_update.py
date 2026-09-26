"""
Monthly update for gse_retained_portfolio.csv.

Pulls the latest month's retained/mortgage-related-investments portfolio
composition from Fannie Mae's "Monthly Summary" (Table 4 - Retained Mortgage
Portfolio Composition) and Freddie Mac's "Monthly Volume Summary" (Table 3 -
Mortgage-Related Investments Portfolio Components), and appends a validated
row per issuer for the next month not yet in the CSV.

Both GSEs publish these reports 25-30 days after month-end (see
.github/workflows/gse_monthly.yml for the observed publish-date evidence and
the resulting schedule), so this is a no-op most days: it downloads nothing
new until that month's PDF actually exists at the predictable URL, and does
nothing (clean exit, no commit) if it's not there yet or the month's row
can't be found/validated - same "leave gaps as gaps, don't guess" philosophy
as nightly_job.py.

Each report also carries a trailing ~13-month history in the same table, but
this script only extracts and appends the single newest month per run - it
doesn't re-verify or overwrite earlier months already in the CSV.
"""
import calendar
import io
import logging
import sys
from datetime import date

import pandas as pd
import pdfplumber
import requests

logger = logging.getLogger("gse_portfolio_update")

CSV_PATH = "gse_retained_portfolio.csv"

FNMA_URL_TEMPLATE = "https://www.fanniemae.com/media/document/pdf/{mm}{dd}{yy}.pdf"
FHLMC_URL_TEMPLATE = "https://www.freddiemac.com/investors/financials/pdf/{mm}{yy}mvs.pdf"

MONTH_NAMES = list(calendar.month_name)[1:]   # 'January' .. 'December'
MONTH_ABBR = list(calendar.month_abbr)[1:]    # 'Jan' .. 'Dec'

# Numbers may not sum exactly due to each report's own stated rounding -
# both source PDFs carry that same caveat (observed up to $1mm off in the
# July 2026 pull). $3mm gives headroom without letting a real parsing error
# (wrong column, shifted row) slip through as "valid".
COMPONENT_SUM_TOLERANCE = 3

# Freddie Mac's Monthly Volume Summary comes at different page sizes from
# month to month - standard letter landscape (792pt wide; e.g. Dec 2025,
# Mar-May 2026, Aug 2026) or oversized (~2933-3046pt; e.g. Jan-Feb, Jun-Jul
# 2026) - with the same layout scaled. parse_fhlmc_table3's position offsets
# were measured on a 2933.33pt page and are scaled by page width / this.
FHLMC_REFERENCE_PAGE_WIDTH = 2933.33


def next_target_month(csv_path=CSV_PATH):
    """(year, month) for the next month to fetch - one past whatever's latest in the CSV."""
    df = pd.read_csv(csv_path, parse_dates=["month"])
    latest = df["month"].max()
    y, m = latest.year, latest.month + 1
    if m > 12:
        m, y = 1, y + 1
    return y, m


def fnma_url(year, month):
    dd = calendar.monthrange(year, month)[1]  # last calendar day of month
    return FNMA_URL_TEMPLATE.format(mm=f"{month:02d}", dd=f"{dd:02d}", yy=f"{year % 100:02d}")


def fhlmc_url(year, month):
    return FHLMC_URL_TEMPLATE.format(mm=f"{month:02d}", yy=f"{year % 100:02d}")


def _download(url):
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.content


def _cluster_rows(words, tol=3.0):
    """Groups words into text-rows by y-position ('top'), tolerant of small jitter."""
    rows = []
    for w in sorted(words, key=lambda w: w["top"]):
        if rows and abs(w["top"] - rows[-1][0]) <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return rows


def _cell_text(row_words, x0, x1, join=""):
    cell = sorted((w for w in row_words if x0 <= w["x0"] < x1), key=lambda w: w["x0"])
    return join.join(w["text"] for w in cell)


def _parse_number(text):
    text = text.strip().replace("$", "").replace(",", "")
    if text in ("", "-", "—"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _find_column_bounds(header_words, label_to_x0, scale=1.0):
    """
    Given words on a page and {header_label: x0_of_that_header_word}, returns
    {header_label: (x0_lo, x0_hi)} - each column's range spans from just left
    of its own header to just left of the next column's header (or +999 for
    the rightmost), so a value column catches split digit-group fragments
    that land a few points left/right of the header word itself (observed in
    Freddie Mac's PDF: some rows render a cell's leading digit as a separate
    text run from the rest, e.g. "3" + "0,630" for "30,630"). scale shrinks
    the 40pt margin for PDFs rendered at a smaller page size (see
    FHLMC_REFERENCE_PAGE_WIDTH).
    """
    ordered = sorted(label_to_x0.items(), key=lambda kv: kv[1])
    bounds = {}
    for i, (label, x0) in enumerate(ordered):
        lo = x0 - 40 * scale
        hi = ordered[i + 1][1] - 40 * scale if i + 1 < len(ordered) else x0 + 999
        bounds[label] = (lo, hi)
    return bounds


def _header_x0(words, text_options, top_range):
    for w in words:
        if top_range[0] <= w["top"] <= top_range[1] and w["text"] in text_options:
            return w["x0"]
    return None


def parse_fnma_table4(pdf_bytes, target_year, target_month):
    """
    Returns {"agency_securities", "non_agency_securities", "mortgage_loans",
    "retained_portfolio_end_balance"} for target_year/target_month, or None
    if that month's row isn't present / doesn't validate.
    """
    target_label = f"{MONTH_NAMES[target_month - 1]} {target_year}"

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            table4_heading = [w for w in words if w["text"] == "4" and 55 <= w["top"] <= 80]
            if not table4_heading:
                continue
            # -85 (not the header word's own x0) because Table 4's month-label column
            # (e.g. "July") starts well to the left of the "Table 4" heading itself -
            # empirically, Table 3 (to its left) tops out ~104pt left of that heading,
            # and Table 4's own label column starts ~60pt left of it, so -85 sits
            # safely in the ~44pt gap between them without hardcoding either table's
            # absolute column positions (which could drift slightly month to month).
            right = [w for w in words if w["x0"] > table4_heading[0]["x0"] - 85]

            header_top_range = (95, 120)
            agency_x0 = _header_x0(right, {"Agency"}, header_top_range)
            nonagency_x0 = _header_x0(right, {"Non-Agency"}, header_top_range)
            loans_x0 = _header_x0(right, {"Loans"}, header_top_range)
            balance_x0 = _header_x0(right, {"Retained"}, header_top_range)
            if None in (agency_x0, nonagency_x0, loans_x0, balance_x0):
                continue

            bounds = _find_column_bounds(
                right,
                {"agency": agency_x0, "non_agency": nonagency_x0, "loans": loans_x0, "balance": balance_x0},
            )
            label_hi = agency_x0 - 40

            for _, row_words in _cluster_rows(right):
                label = _cell_text(row_words, 0, label_hi, join=" ").strip()
                if label != target_label:
                    continue
                agency = _parse_number(_cell_text(row_words, *bounds["agency"]))
                non_agency = _parse_number(_cell_text(row_words, *bounds["non_agency"]))
                loans = _parse_number(_cell_text(row_words, *bounds["loans"]))
                balance = _parse_number(_cell_text(row_words, *bounds["balance"]))
                if None in (agency, non_agency, loans, balance):
                    logger.warning("FNMA %s: found row but couldn't parse all 4 numbers: %r", target_label, row_words)
                    return None
                if abs((agency + non_agency + loans) - balance) > COMPONENT_SUM_TOLERANCE:
                    logger.warning(
                        "FNMA %s: components (%s + %s + %s) don't sum to reported balance %s - not trusting this parse",
                        target_label, agency, non_agency, loans, balance,
                    )
                    return None
                return {
                    "agency_securities": agency,
                    "non_agency_securities": non_agency,
                    "mortgage_loans": loans,
                    "retained_portfolio_end_balance": balance,
                }
    return None


def parse_fhlmc_table3(pdf_bytes, target_year, target_month):
    target_abbr = MONTH_ABBR[target_month - 1]

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            # The offsets below were measured on the older, oversized page
            # (FHLMC_REFERENCE_PAGE_WIDTH); scale them to this page's width.
            scale = page.width / FHLMC_REFERENCE_PAGE_WIDTH
            # "TABLE 3" heading - require the "TABLE" word just left of the "3", since
            # split digit runs in other tables (e.g. "3" + "0,630") also yield bare "3"s.
            table_words = [w for w in words if w["text"] == "TABLE"]
            heading3 = [
                w for w in words
                if w["text"] == "3" and w["top"] < 1200 * scale and w["x0"] > 1200 * scale
                and any(abs(t["top"] - w["top"]) < 2 * scale and 0 <= w["x0"] - t["x1"] < 20 * scale for t in table_words)
            ]
            if not heading3:
                continue
            # -70: see the matching comment in parse_fnma_table4 - Table 2 (to Table 3's
            # left) tops out ~83pt left of the "TABLE 3" heading, and Table 3's own
            # month-label column starts ~58pt left of it; -70 sits in that gap.
            right = [w for w in words if w["x0"] > heading3[0]["x0"] - 70 * scale]

            header_top_range = (heading3[0]["top"] + 40 * scale, heading3[0]["top"] + 130 * scale)
            agency_x0 = _header_x0(right, {"Agency"}, header_top_range)
            nonagency_x0 = _header_x0(right, {"Non-Agency"}, header_top_range)
            loans_x0 = _header_x0(right, {"Loans"}, header_top_range)
            balance_x0 = _header_x0(right, {"Ending"}, header_top_range)
            if None in (agency_x0, nonagency_x0, loans_x0, balance_x0):
                continue

            bounds = _find_column_bounds(
                right,
                {"agency": agency_x0, "non_agency": nonagency_x0, "loans": loans_x0, "balance": balance_x0},
                scale=scale,
            )
            label_hi = agency_x0 - 40 * scale

            # Freddie Mac's table only spells out the year on each calendar year's
            # first row ("Jul 2025", then bare "Aug", "Sep", ... "Dec", then "Jan
            # 2026" resets it, then bare "Feb" .. "Jul"). Carry the last-seen year
            # forward for month-only rows instead of requiring an exact "Mon YYYY"
            # label match.
            current_year = None
            target_label = f"{target_abbr} {target_year}"
            for _, row_words in _cluster_rows(right):
                label = _cell_text(row_words, 0, label_hi, join=" ").strip()
                parts = label.split()
                if not parts or parts[0] not in MONTH_ABBR:
                    continue  # not a month-data row (heading/highlights text/Full-Year/YTD/etc.)
                month_abbr = parts[0]
                if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
                    current_year = int(parts[1])
                if current_year is None or month_abbr != target_abbr or current_year != target_year:
                    continue
                agency = _parse_number(_cell_text(row_words, *bounds["agency"]))
                non_agency = _parse_number(_cell_text(row_words, *bounds["non_agency"]))
                loans = _parse_number(_cell_text(row_words, *bounds["loans"]))
                balance = _parse_number(_cell_text(row_words, *bounds["balance"]))
                if (agency, non_agency, loans, balance) == (None, None, None, None):
                    continue  # a bare "May 2026"-style title line, not the data row
                if None in (agency, non_agency, loans, balance):
                    logger.warning("FHLMC %s: found row but couldn't parse all 4 numbers: %r", target_label, row_words)
                    return None
                if abs((agency + non_agency + loans) - balance) > COMPONENT_SUM_TOLERANCE:
                    logger.warning(
                        "FHLMC %s: components (%s + %s + %s) don't sum to reported balance %s - not trusting this parse",
                        target_label, agency, non_agency, loans, balance,
                    )
                    return None
                return {
                    "agency_securities": agency,
                    "non_agency_securities": non_agency,
                    "mortgage_loans": loans,
                    "retained_portfolio_end_balance": balance,
                }
    return None


def append_month(year, month, fnma_row, fhlmc_row, csv_path=CSV_PATH):
    month_str = date(year, month, 1).isoformat()[:7] + "-01"
    df = pd.read_csv(csv_path)
    new_rows = pd.DataFrame(
        [
            {"month": month_str, "issuer": "FNMA", **fnma_row},
            {"month": month_str, "issuer": "FHLMC", **fhlmc_row},
        ]
    )
    out = pd.concat([df, new_rows], ignore_index=True)
    out.to_csv(csv_path, index=False)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    year, month = next_target_month()
    label = f"{MONTH_NAMES[month - 1]} {year}"
    logger.info("Checking for %s data", label)

    fnma_pdf = _download(fnma_url(year, month))
    fhlmc_pdf = _download(fhlmc_url(year, month))

    if fnma_pdf is None:
        logger.info("Fannie Mae %s report not published yet (%s) - nothing to do.", label, fnma_url(year, month))
        return
    if fhlmc_pdf is None:
        logger.info("Freddie Mac %s report not published yet (%s) - nothing to do.", label, fhlmc_url(year, month))
        return

    fnma_row = parse_fnma_table4(fnma_pdf, year, month)
    fhlmc_row = parse_fhlmc_table3(fhlmc_pdf, year, month)

    if fnma_row is None or fhlmc_row is None:
        logger.error(
            "Both PDFs downloaded but %s couldn't be parsed/validated for %s - not writing anything. "
            "This needs a human look (report layout may have changed).",
            "Fannie Mae" if fnma_row is None else "Freddie Mac", label,
        )
        sys.exit(1)

    append_month(year, month, fnma_row, fhlmc_row)
    logger.info("Appended %s: FNMA=%s FHLMC=%s", label, fnma_row, fhlmc_row)


if __name__ == "__main__":
    main()
