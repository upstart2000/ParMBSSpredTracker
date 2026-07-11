"""
MBS Spread Tracker - Streamlit dashboard.

Reads directly from the repo's mbs_spreads.db (populated by nightly_job.py /
backfill.py). Designed to run unmodified on Streamlit Community Cloud: the db
path is relative to the repo root, which is the working directory both
locally and on Cloud.
"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db

# Fixed categorical order (validated for CVD separation - see dataviz skill palette).
COLOR_SPREAD_5YR = "#2a78d6"   # blue
COLOR_SPREAD_10YR = "#1baf7a"  # aqua
COLOR_SPREAD_AVG = "#eda100"   # yellow
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


DAILY_TABLE_COLUMNS = [
    ("ust_5yr", "UST 5yr (%)"),
    ("ust_10yr", "UST 10yr (%)"),
    ("coupon_low", "UMBS Low Coupon (%)"),
    ("price_low", "UMBS Low Price"),
    ("coupon_high", "UMBS High Coupon (%)"),
    ("price_high", "UMBS High Price"),
    ("par_coupon", "Par Coupon (%)"),
    ("spread_5yr", "Spread vs 5yr (bps)"),
    ("spread_10yr", "Spread vs 10yr (bps)"),
    ("spread_avg", "Spread vs Avg (bps)"),
]


def build_daily_table(prior_row, today_row):
    def extract(row):
        if row is None:
            return {label: None for _, label in DAILY_TABLE_COLUMNS}
        return {label: row.get(key) for key, label in DAILY_TABLE_COLUMNS}

    prior_vals = extract(prior_row)
    today_vals = extract(today_row)
    delta_vals = {
        label: (today_vals[label] - prior_vals[label])
        if (today_vals[label] is not None and prior_vals[label] is not None)
        else None
        for label in today_vals
    }

    return pd.DataFrame([prior_vals, today_vals, delta_vals], index=["Prior Day", "Today", "Delta"])


def style_daily_table(table_df):
    def highlight_delta_row(row):
        if row.name != "Delta":
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

    return table_df.style.format(precision=2, na_rep="—").apply(highlight_delta_row, axis=1)


def qtd_metric(label, value, unit, qtd_chg):
    value_str = f"{value:.2f}{unit}" if value is not None else "—"
    delta_str = f"{qtd_chg:+.1f} bps QTD" if qtd_chg is not None else None
    st.metric(label=label, value=value_str, delta=delta_str)


st.title("MBS Spread Tracker")

df = load_dataframe()

if df.empty:
    st.warning(f"No data found in {db.DEFAULT_DB_PATH} yet. Run backfill.py and/or nightly_job.py first.")
    st.stop()

latest_rows = df.tail(2).to_dict("records")
today_row = latest_rows[-1]
prior_row = latest_rows[-2] if len(latest_rows) > 1 else None

st.caption(
    f"Most recent trading day: **{today_row['finra_date'].date()}** "
    + (f"(prior: {prior_row['finra_date'].date()})" if prior_row else "(no prior day in dataset yet)")
)

# --- QTD change section (prominent, up top) ---
st.subheader("Quarter-to-Date Change")

if today_row.get("qtd_ref_date") is None:
    st.info("No prior-quarter baseline available yet for this dataset - QTD change can't be computed for the current quarter's first stretch of data.")
else:
    ref_date = pd.to_datetime(today_row["qtd_ref_date"]).date()
    st.caption(f"vs. quarter-start reference: **{ref_date}**")
    cols = st.columns(5)
    with cols[0]:
        qtd_metric("5yr UST", today_row.get("ust_5yr"), "%", today_row.get("qtd_chg_ust_5yr"))
    with cols[1]:
        qtd_metric("10yr UST", today_row.get("ust_10yr"), "%", today_row.get("qtd_chg_ust_10yr"))
    with cols[2]:
        qtd_metric("Spread vs 5yr", today_row.get("spread_5yr"), " bps", today_row.get("qtd_chg_spread_5yr"))
    with cols[3]:
        qtd_metric("Spread vs 10yr", today_row.get("spread_10yr"), " bps", today_row.get("qtd_chg_spread_10yr"))
    with cols[4]:
        qtd_metric("Spread vs Avg", today_row.get("spread_avg"), " bps", today_row.get("qtd_chg_spread_avg"))

st.divider()

# --- Daily table ---
st.subheader("Daily Snapshot")
daily_table = build_daily_table(prior_row, today_row)
st.dataframe(style_daily_table(daily_table), width="stretch")
st.caption("Delta = Today − Prior Day. Note: if the near-month settlement rolled between the two days, the UMBS bracket coupons may differ, so their delta reflects the roll as well as any price move.")

st.divider()

# --- Historical chart ---
st.subheader("Historical Spread")

fig = go.Figure()
series = [
    ("spread_5yr", "Spread vs 5yr", COLOR_SPREAD_5YR),
    ("spread_10yr", "Spread vs 10yr", COLOR_SPREAD_10YR),
    ("spread_avg", "Spread vs Avg(5,10)", COLOR_SPREAD_AVG),
]
for col, name, color in series:
    fig.add_trace(
        go.Scatter(
            x=df["finra_date"],
            y=df[col],
            mode="lines+markers",
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

with st.expander("Show underlying data"):
    st.dataframe(df, width="stretch")
