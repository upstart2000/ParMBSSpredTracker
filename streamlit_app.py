"""
MBS Spread Tracker - Streamlit dashboard.

Reads directly from the repo's mbs_spreads.db (populated by nightly_job.py /
backfill.py). Designed to run unmodified on Streamlit Community Cloud: the db
path is relative to the repo root, which is the working directory both
locally and on Cloud.

Second tab (GSE Retained Portfolios) reads gse_retained_portfolio.csv - a
separate, manually-curated dataset (not part of the nightly FINRA/Treasury
pipeline). See that file's own header for provenance/refresh notes.
"""
import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db

# Fixed categorical order (validated for CVD separation - see dataviz skill palette).
COLOR_SPREAD_5YR = "#2a78d6"   # blue
COLOR_SPREAD_10YR = "#1baf7a"  # aqua
COLOR_SPREAD_AVG = "#eda100"   # yellow
COLOR_UST_10YR = "#eb6834"     # orange (next unused categorical slot)
COLOR_UP = "#006300"
COLOR_DOWN = "#e34948"
GRIDLINE = "#e1e0d9"
MUTED_INK = "#898781"

st.set_page_config(page_title="MBS Spread Tracker", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def _load_dataframe(db_path, mtime):
    rows = db.get_all(db_path)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["finra_date"] = pd.to_datetime(df["finra_date"])
        df = df.sort_values("finra_date")
    return df


def load_dataframe(db_path=db.DEFAULT_DB_PATH):
    mtime = os.path.getmtime(db_path) if os.path.exists(db_path) else None
    return _load_dataframe(db_path, mtime)


SERIES_OPTIONS = {
    "Normalized (30-day constant-maturity)": "normalized",
    "Raw (near-month settlement)": "raw",
}


SPREAD_COLUMNS = ["Spread vs 5yr (bps)", "Spread vs 10yr (bps)", "Spread vs 5/10yr (bps)"]
COMPUTED_ROW_LABELS = {"Daily Change", "Prior Quarter Change", "QTD Change"}

GSE_PORTFOLIO_CSV = "gse_retained_portfolio.csv"
ISSUER_LABELS = {"FNMA": "Fannie Mae", "FHLMC": "Freddie Mac"}


# Historical chart window presets -> months back from the latest date (None = all history).
RANGE_PRESETS = {"1M": 1, "3M": 3, "6M": 6, "YTD": "ytd", "1Y": 12, "2Y": 24, "5Y": 60, "All": None}
DEFAULT_RANGE_PRESET = "1Y"
MAX_POINTS_WITH_MARKERS = 130  # ~6 months of trading days


def preset_window(preset, data_start, data_end):
    """(start, end) dates for a RANGE_PRESETS key, clamped to the data's own range."""
    months = RANGE_PRESETS[preset]
    if months is None:
        start = data_start
    elif months == "ytd":
        start = date(data_end.year, 1, 1)
    else:
        start = (pd.Timestamp(data_end) - pd.DateOffset(months=months)).date()
    return max(start, data_start), data_end


def range_preset_control(prefix, data_start, data_end):
    """
    Renders a chart's 1M..All preset buttons (keys "<prefix>_range_preset" /
    "<prefix>_range_slider") and returns its current (start, end) window.
    Pair with range_slider_control(prefix, ...) below the chart. Each prefix
    keeps its own independent window.
    """
    preset_key, slider_key = f"{prefix}_range_preset", f"{prefix}_range_slider"
    if slider_key not in st.session_state:
        st.session_state[preset_key] = DEFAULT_RANGE_PRESET
        st.session_state[slider_key] = preset_window(DEFAULT_RANGE_PRESET, data_start, data_end)

    def _apply_preset():
        preset = st.session_state.get(preset_key)
        if preset is not None:
            st.session_state[slider_key] = preset_window(preset, data_start, data_end)

    st.segmented_control(
        "Date range",
        options=list(RANGE_PRESETS.keys()),
        key=preset_key,
        on_change=_apply_preset,
        label_visibility="collapsed",
    )

    # A slider value saved in an earlier session can fall outside today's data
    # range (e.g. the db grew); clamp it rather than letting st.slider error.
    window_start, window_end = st.session_state[slider_key]
    window_start = min(max(window_start, data_start), data_end)
    window_end = min(max(window_end, window_start), data_end)
    st.session_state[slider_key] = (window_start, window_end)
    return window_start, window_end


def range_slider_control(prefix, data_start, data_end):
    """The date-range slider under a chart; dragging it clears that chart's preset button."""
    def _clear_preset():
        st.session_state[f"{prefix}_range_preset"] = None

    st.slider(
        "Chart period",
        min_value=data_start,
        max_value=data_end,
        key=f"{prefix}_range_slider",
        on_change=_clear_preset,
        format="MMM D, YYYY",
        label_visibility="collapsed",
    )


def window_rows(df, window_start, window_end):
    """Rows of df within [window_start, window_end], plus the trace mode for that many points."""
    rows = df[(df["finra_date"].dt.date >= window_start) & (df["finra_date"].dt.date <= window_end)]
    # Diamond markers are readable for a few months of dailies; across years they
    # turn into a solid smear, so drop to plain lines past that.
    mode = "lines+markers" if len(rows) <= MAX_POINTS_WITH_MARKERS else "lines"
    return rows, mode


def _row_date(row):
    """row['finra_date'] may be a pandas Timestamp (rows from the df) or an ISO string (rows straight from db.py)."""
    d = row["finra_date"]
    return d.date() if hasattr(d, "date") else date.fromisoformat(d)


def _quarter_label(d):
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def bracket_coupons(row, suffix):
    if row is None:
        return set()
    # pd.notna (not a plain `- {None}` set-difference) because a missing bracket
    # bound comes through as NaN when row is sourced from the pandas df (today_row/
    # prior_row), not None - and NaN != NaN, so a bare set-difference against {None}
    # lets distinct NaNs pile up across rows, producing duplicate "UMBS nan" columns
    # that pandas' Styler.apply then rejects as non-unique.
    return {c for c in (row.get(f"coupon_low_{suffix}"), row.get(f"coupon_high_{suffix}")) if pd.notna(c)}


def snapshot_row_values(row, suffix, coupon_union):
    if row is None:
        return None
    curve = db.parse_coupon_curve(row.get(f"coupon_curve_{suffix}"))
    values = {
        "UST 5yr": row.get("ust_5yr"),
        "UST 10yr": row.get("ust_10yr"),
    }
    for c in coupon_union:
        values[f"UMBS {c:.1f}"] = curve.get(c)
    values["Par Coupon"] = row.get(f"par_coupon_{suffix}")
    values["Spread vs 5yr (bps)"] = row.get(f"spread_5yr_{suffix}")
    values["Spread vs 10yr (bps)"] = row.get(f"spread_10yr_{suffix}")
    values["Spread vs 5/10yr (bps)"] = row.get(f"spread_avg_{suffix}")
    return values


def diff_row(values_a, values_b, columns, scope_columns=None):
    """values_a - values_b, restricted to scope_columns if given (else every column)."""
    result = {}
    for col in columns:
        if scope_columns is not None and col not in scope_columns:
            result[col] = None
            continue
        a, b = values_a.get(col), values_b.get(col)
        result[col] = (a - b) if (a is not None and b is not None) else None
    return result


def build_daily_table(today_row, prior_row, current_qe, prior_qe, suffix):
    """
    Rows, in order: prior quarter-end, current quarter-end, Prior Quarter
    Change (current QE - prior QE), the two most recent stored trading days
    (labeled with their actual dates), Daily Change (latest - prior), QTD
    Change (latest - current QE). Any row whose source data isn't available
    yet (e.g. no quarter-end baseline this early in the dataset) is omitted
    rather than shown empty.
    """
    coupon_union = sorted(
        bracket_coupons(today_row, suffix)
        | bracket_coupons(prior_row, suffix)
        | bracket_coupons(current_qe, suffix)
        | bracket_coupons(prior_qe, suffix)
    )
    columns = (
        ["UST 5yr", "UST 10yr"]
        + [f"UMBS {c:.1f}" for c in coupon_union]
        + ["Par Coupon"] + SPREAD_COLUMNS
    )

    today_vals = snapshot_row_values(today_row, suffix, coupon_union)
    prior_vals = snapshot_row_values(prior_row, suffix, coupon_union)
    current_qe_vals = snapshot_row_values(current_qe, suffix, coupon_union)
    prior_qe_vals = snapshot_row_values(prior_qe, suffix, coupon_union)

    rows = {}
    if prior_qe_vals is not None:
        rows[f"{_quarter_label(_row_date(prior_qe))} End ({_row_date(prior_qe)})"] = prior_qe_vals
    if current_qe_vals is not None:
        rows[f"{_quarter_label(_row_date(current_qe))} End ({_row_date(current_qe)})"] = current_qe_vals
    if current_qe_vals is not None and prior_qe_vals is not None:
        rows["Prior Quarter Change"] = diff_row(current_qe_vals, prior_qe_vals, columns)
    if prior_vals is not None:
        rows[_row_date(prior_row).isoformat()] = prior_vals
    rows[_row_date(today_row).isoformat()] = today_vals
    if prior_vals is not None:
        rows["Daily Change"] = diff_row(today_vals, prior_vals, columns)
    if current_qe_vals is not None:
        rows["QTD Change"] = diff_row(today_vals, current_qe_vals, columns)

    return pd.DataFrame.from_dict(rows, orient="index", columns=columns)


def style_daily_table(table_df):
    def highlight_computed_rows(row):
        if row.name not in COMPUTED_ROW_LABELS:
            return ["" for _ in row]
        styles = []
        for v in row:
            if pd.isna(v):
                styles.append("")
            elif v > 0:
                styles.append(f"color:{COLOR_UP}; font-weight:600")
            elif v < 0:
                styles.append(f"color:{COLOR_DOWN}; font-weight:600")
            else:
                styles.append("")
        return styles

    spread_cols = [c for c in table_df.columns if c in SPREAD_COLUMNS]
    other_cols = [c for c in table_df.columns if c not in SPREAD_COLUMNS]
    return (
        table_df.style
        .format(precision=2, na_rep="—", subset=other_cols)
        .format(precision=0, na_rep="—", subset=spread_cols)
        .apply(highlight_computed_rows, axis=1)
    )


def qtd_metric(label, value, unit, qtd_chg):
    precision = 0 if unit.strip() == "bps" else 2
    value_str = f"{value:.{precision}f}{unit}" if value is not None else "—"
    delta_str = f"{qtd_chg:+.0f} bps QTD" if qtd_chg is not None else None
    st.metric(label=label, value=value_str, delta=delta_str)


@st.cache_data(show_spinner=False)
def _load_gse_portfolio(csv_path, mtime):
    df = pd.read_csv(csv_path, parse_dates=["month"])
    return df


def load_gse_portfolio(csv_path=GSE_PORTFOLIO_CSV):
    mtime = os.path.getmtime(csv_path) if os.path.exists(csv_path) else None
    return _load_gse_portfolio(csv_path, mtime)


def gse_wide(gse_df, value_col):
    """Pivot to one column per issuer plus a Sum column, indexed by month, sorted chronologically."""
    wide = gse_df.pivot(index="month", columns="issuer", values=value_col).sort_index()
    wide["Sum"] = wide.get("FNMA", 0) + wide.get("FHLMC", 0)
    return wide


def gse_portfolio_chart(wide_df, yaxis_title):
    fig = go.Figure()
    series = [
        ("FNMA", "Fannie Mae", COLOR_SPREAD_5YR),
        ("FHLMC", "Freddie Mac", COLOR_SPREAD_10YR),
        ("Sum", "Fannie Mae + Freddie Mac", COLOR_SPREAD_AVG),
    ]
    for col, name, color in series:
        fig.add_trace(
            go.Scatter(
                x=wide_df.index,
                y=wide_df[col],
                mode="lines+markers",
                name=name,
                line=dict(color=color, width=2),
                marker=dict(symbol="diamond-open", size=8, line=dict(width=1.5, color=color)),
            )
        )
    fig.update_layout(
        xaxis_title="Month",
        yaxis_title=yaxis_title,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=60, b=40),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=True, zerolinecolor=GRIDLINE)
    return fig


tab1, tab2 = st.tabs(["MBS Spread Tracker", "GSE Retained Portfolios"])

with tab1:
    st.title("MBS Spread Tracker")

    df = load_dataframe()

    if df.empty:
        st.warning(f"No data found in {db.DEFAULT_DB_PATH} yet. Run backfill.py and/or nightly_job.py first.")
        st.stop()

    latest_rows = df.tail(2).to_dict("records")
    today_row = latest_rows[-1]
    prior_row = latest_rows[-2] if len(latest_rows) > 1 else None

    # Gap-detection logic stays fully intact and still runs here - only its display
    # moved (to the bottom of the page, see the end of this script).
    gap_warning = None
    if prior_row is not None:
        gap_days = (today_row["finra_date"].date() - prior_row["finra_date"].date()).days
        if gap_days > db.MAX_EXPECTED_GAP_DAYS:
            gap_warning = (
                f"⚠️ Data gap: {gap_days} calendar days between {prior_row['finra_date'].date()} and "
                f"{today_row['finra_date'].date()} - wider than a normal weekend/holiday, so one or more "
                "trading days are missing from the dataset. The 'Delta' row below and the QTD changes "
                "reflect the full gap, not a single day's move."
            )

    # The Raw/Normalized selector widget itself renders further down (near the
    # historical chart), but its value is needed up here for the QTD section and
    # Daily Snapshot table. Streamlit persists widget state in session_state
    # across reruns, so reading it via the widget's key before the widget is
    # instantiated later in this same run still reflects the current selection.
    DEFAULT_SERIES_CHOICE = next(iter(SERIES_OPTIONS))
    series_choice = st.session_state.get("series_choice_widget", DEFAULT_SERIES_CHOICE)
    suffix = SERIES_OPTIONS[series_choice]

    # --- QTD change section (prominent, up top) ---
    if today_row.get("qtd_ref_date") is None:
        st.info("No prior-quarter baseline available yet for this dataset - QTD change can't be computed for the current quarter's first stretch of data.")
    else:
        cols = st.columns(5)
        with cols[0]:
            qtd_metric("5yr UST", today_row.get("ust_5yr"), "%", today_row.get("qtd_chg_ust_5yr"))
        with cols[1]:
            qtd_metric("10yr UST", today_row.get("ust_10yr"), "%", today_row.get("qtd_chg_ust_10yr"))
        with cols[2]:
            qtd_metric("Spread vs 5yr", today_row.get(f"spread_5yr_{suffix}"), " bps", today_row.get(f"qtd_chg_spread_5yr_{suffix}"))
        with cols[3]:
            qtd_metric("Spread vs 10yr", today_row.get(f"spread_10yr_{suffix}"), " bps", today_row.get(f"qtd_chg_spread_10yr_{suffix}"))
        with cols[4]:
            qtd_metric("Spread vs 5/10yr", today_row.get(f"spread_avg_{suffix}"), " bps", today_row.get(f"qtd_chg_spread_avg_{suffix}"))

    st.divider()

    # --- Daily table ---
    current_qe, prior_qe = db.get_quarter_end_rows(today_row["finra_date"].date())
    daily_table = build_daily_table(today_row, prior_row, current_qe, prior_qe, suffix)
    st.dataframe(style_daily_table(daily_table), width="stretch")

    st.divider()

    # --- Historical chart ---
    st.subheader("Historical Spread")

    st.radio(
        "Par coupon / spread series",
        options=list(SERIES_OPTIONS.keys()),
        horizontal=True,
        key="series_choice_widget",
        help=(
            "Raw uses whichever settlement month is nearest today - the implied price drifts as "
            "days-to-settlement shrink toward the next roll, then jumps at the roll (a sawtooth "
            "artifact on top of real spread movement). Normalized interpolates near/next month "
            "prices to a fixed 30-day-to-settlement horizon, removing that artifact."
        ),
    )

    # --- Chart date range: preset buttons above, slider below ---
    # Filtering the dataframe (rather than zooming a Plotly rangeslider) keeps
    # the y-axis autoscaled to whatever window is shown - with ~10 years of
    # history, a 1M view would otherwise be squashed flat against the full
    # history's y-range.
    data_start = df["finra_date"].iloc[0].date()
    data_end = df["finra_date"].iloc[-1].date()
    window_start, window_end = range_preset_control("chart", data_start, data_end)
    chart_df, trace_mode = window_rows(df, window_start, window_end)

    fig = go.Figure()
    series = [
        (f"spread_5yr_{suffix}", "Spread vs 5yr", COLOR_SPREAD_5YR),
        (f"spread_10yr_{suffix}", "Spread vs 10yr", COLOR_SPREAD_10YR),
        (f"spread_avg_{suffix}", "Spread vs 5/10yr", COLOR_SPREAD_AVG),
    ]
    for col, name, color in series:
        fig.add_trace(
            go.Scatter(
                x=chart_df["finra_date"],
                y=chart_df[col],
                mode=trace_mode,
                name=name,
                line=dict(color=color, width=2),
                marker=dict(symbol="diamond-open", size=8, line=dict(width=1.5, color=color)),
            )
        )

    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Spread (bps)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=60, b=40),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=True, zerolinecolor=GRIDLINE)

    st.plotly_chart(fig, width="stretch")

    range_slider_control("chart", data_start, data_end)

    # Days inside a documented known gap (db.KNOWN_DATA_GAPS) are explained
    # once below, not counted as unexpected missing days.
    in_known_gap = df["finra_date"].dt.date.map(db.in_known_gap)
    if suffix == "normalized":
        missing_count = int((df[f"par_coupon_{suffix}"].isna() & ~in_known_gap).sum())
        if missing_count:
            st.caption(
                f"Normalized par coupon isn't computable for {missing_count} historical day(s) "
                "(missing/thin next-month data that day) - those show as gaps in the lines above, "
                "not zeros or an error."
            )
    visible_gaps = [
        f"{gap_start:%b %d, %Y} - {gap_end:%b %d, %Y} ({reason})"
        for gap_start, gap_end, reason in db.KNOWN_DATA_GAPS
        if gap_start <= window_end and gap_end >= window_start
    ]
    if visible_gaps:
        st.caption("Known gaps in the lines above: " + "; ".join(visible_gaps) + ".")

    # --- Spread vs 10yr with the 10yr UST on a secondary y-axis ---
    # Has its own range presets/slider, independent of the chart above.
    st.subheader("Spread vs 10yr and the 10yr UST")
    rates_start, rates_end = range_preset_control("rates_chart", data_start, data_end)
    rates_df, rates_mode = window_rows(df, rates_start, rates_end)
    rates_fig = go.Figure()
    for y_col, name, color, yaxis in [
        (f"spread_10yr_{suffix}", "Spread vs 10yr (bps)", COLOR_SPREAD_10YR, "y"),
        ("ust_10yr", "10yr UST (%)", COLOR_UST_10YR, "y2"),
    ]:
        rates_fig.add_trace(
            go.Scatter(
                x=rates_df["finra_date"],
                y=rates_df[y_col],
                mode=rates_mode,
                name=name,
                yaxis=yaxis,
                line=dict(color=color, width=2),
                marker=dict(symbol="diamond-open", size=8, line=dict(width=1.5, color=color)),
            )
        )
    rates_fig.update_layout(
        xaxis_title="Date",
        xaxis_hoverformat="%b %d, %Y",
        yaxis=dict(title_text="Spread vs 10yr (bps)", showgrid=True, gridcolor=GRIDLINE, zeroline=False),
        yaxis2=dict(title_text="10yr UST (%)", overlaying="y", side="right", showgrid=False, zeroline=False),
        hovermode="x unified",
        hoverlabel=dict(namelength=-1),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=60, b=40),
    )
    rates_fig.update_xaxes(showgrid=True, gridcolor=GRIDLINE, zeroline=False)
    st.plotly_chart(rates_fig, width="stretch")
    range_slider_control("rates_chart", data_start, data_end)

    with st.expander("Show underlying data"):
        curve_cols = ["coupon_curve_raw", "coupon_curve_normalized"]
        st.dataframe(df.drop(columns=curve_cols), width="stretch")

    st.divider()
    if gap_warning:
        st.warning(gap_warning)

with tab2:
    st.title("GSE Retained Portfolios")
    st.caption(
        "Monthly retained/mortgage-related-investments portfolio composition for Fannie Mae and "
        "Freddie Mac, sourced from each GSE's own monthly investor summary - Fannie Mae \"Monthly "
        "Summary\" Table 4 (Retained Mortgage Portfolio Composition) and Freddie Mac \"Monthly "
        "Volume Summary\" Table 3 (Mortgage-Related Investments Portfolio Components). Pulled once "
        "from the July 2026 editions of each, which carried a trailing 13-month history "
        "(July 2025-July 2026); not part of the nightly FINRA/Treasury pipeline, so it won't update "
        "on its own - see gse_retained_portfolio.csv to refresh from a later monthly summary."
    )

    gse_df = load_gse_portfolio()

    if gse_df.empty:
        st.warning(f"No data found in {GSE_PORTFOLIO_CSV}.")
    else:
        agency_billions = gse_wide(gse_df, "agency_securities") / 1000
        balance_billions = gse_wide(gse_df, "retained_portfolio_end_balance") / 1000

        st.subheader("Agency Securities Holdings")
        st.plotly_chart(
            gse_portfolio_chart(agency_billions, "Agency securities ($ billions)"),
            width="stretch",
        )

        st.subheader("Retained Mortgage Portfolio Balance")
        st.plotly_chart(
            gse_portfolio_chart(balance_billions, "Retained portfolio end balance ($ billions)"),
            width="stretch",
        )

        with st.expander("Show underlying data"):
            display_df = gse_df.copy()
            display_df["month"] = display_df["month"].dt.strftime("%Y-%m")
            display_df["issuer"] = display_df["issuer"].map(ISSUER_LABELS).fillna(display_df["issuer"])
            st.dataframe(display_df, width="stretch")
