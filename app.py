"""
app.py
======
Streamlit dashboard for the Revenue Leakage Detection project.

Usage
-----
  streamlit run app.py          (from project root)

Data sources (all pre-computed; no SQL re-runs here)
-----------------------------------------------------
  outputs/scored_invoices.csv
  outputs/vendor_risk.csv
  outputs/evaluation_metrics.csv
  config.yaml
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yaml
from pathlib import Path
from datetime import date

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Revenue Leakage Detection",
    page_icon=":mag:",
    layout="wide",
)

ROOT = Path(__file__).parent

# ── Helper: Indian money format ───────────────────────────────────────────────
def fmt_inr(amount: float) -> str:
    """
    Format a rupee amount in Indian style.
    >= 1 crore  -> X.XX Cr
    >= 1 lakh   -> X.XX L
    otherwise   -> Rs X,XXX
    """
    if amount >= 1e7:
        return f"Rs {amount/1e7:.2f} Cr"
    elif amount >= 1e5:
        return f"Rs {amount/1e5:.2f} L"
    else:
        return f"Rs {amount:,.0f}"


# ── Load data (cached) ────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    scored  = pd.read_csv(ROOT / "outputs" / "scored_invoices.csv")
    vendor  = pd.read_csv(ROOT / "outputs" / "vendor_risk.csv")
    metrics = pd.read_csv(ROOT / "outputs" / "evaluation_metrics.csv")

    with open(ROOT / "config.yaml", "r") as f:
        cfg = yaml.safe_load(f)

    scored["invoice_date"] = pd.to_datetime(scored["invoice_date"])
    scored["payment_date"] = pd.to_datetime(scored["payment_date"])
    return scored, vendor, metrics, cfg


scored_full, vendor_full, metrics_df, cfg = load_data()

# ── Build "triggered rules" string per invoice ────────────────────────────────
@st.cache_data
def build_triggered_rules(df: pd.DataFrame) -> pd.Series:
    rule_cols = {
        "flag_duplicate":       "Dup",
        "flag_split_invoice":   "Split",
        "flag_weekend_holiday": "Wknd",
        "flag_round_figure":    "Round",
        "flag_outlier":         "Outlier",
    }
    def row_rules(row):
        return ", ".join(label for col, label in rule_cols.items() if row[col] == 1) or "-"
    return df.apply(row_rules, axis=1)

scored_full["triggered_rules"] = build_triggered_rules(scored_full)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_main, tab_quality = st.tabs(["Dashboard", "Model Quality"])

# ════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD TAB
# ════════════════════════════════════════════════════════════════════════════
with tab_main:
    st.title("Revenue Leakage Detection Dashboard")

    # ── Sidebar filters ───────────────────────────────────────────────────────
    st.sidebar.header("Filters")

    all_risk_levels = ["HIGH", "MEDIUM", "LOW"]
    sel_risk = st.sidebar.multiselect(
        "Risk level", all_risk_levels, default=all_risk_levels
    )

    all_depts = sorted(scored_full["department"].dropna().unique())
    sel_dept  = st.sidebar.multiselect(
        "Department", all_depts, default=all_depts
    )

    min_date = scored_full["invoice_date"].min().date()
    max_date = scored_full["invoice_date"].max().date()
    date_range = st.sidebar.date_input(
        "Invoice date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    # Handle both 1-element and 2-element tuples
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        d_start, d_end = date_range
    else:
        d_start, d_end = min_date, max_date

    # ── Apply filters ─────────────────────────────────────────────────────────
    mask = (
        scored_full["risk_level"].isin(sel_risk) &
        scored_full["department"].isin(sel_dept) &
        (scored_full["invoice_date"].dt.date >= d_start) &
        (scored_full["invoice_date"].dt.date <= d_end)
    )
    scored = scored_full[mask].copy()

    # Re-aggregate vendor table for filtered data
    vendor = (
        scored.groupby(["vendor_id", "vendor_name"])
        .agg(
            invoice_count         = ("invoice_id",            "count"),
            flagged_count         = ("flag_count",             lambda x: (x > 0).sum()),
            total_spend_inr       = ("amount_inr",             "sum"),
            potential_leakage_inr = ("potential_leakage_inr",  "sum"),
            exposure_at_risk_inr  = ("exposure_at_risk_inr",   "sum"),
            avg_risk_score        = ("risk_score",             "mean"),
            max_risk_score        = ("risk_score",             "max"),
        )
        .reset_index()
    )
    vendor["avg_risk_score"] = vendor["avg_risk_score"].round(1)

    # ── KPI row ───────────────────────────────────────────────────────────────
    total_inv   = len(scored)
    total_spend = scored["amount_inr"].sum()
    pot_leak    = scored["potential_leakage_inr"].sum()
    exposure    = scored["exposure_at_risk_inr"].sum()
    pct_flagged = (scored["flag_count"] > 0).mean() * 100

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Invoices", f"{total_inv:,}")
    k2.metric("Total Spend", fmt_inr(total_spend))
    k3.metric("Potential Leakage (Flagged)", fmt_inr(pot_leak))
    k3.caption("Before audit review; includes false positives")
    k4.metric("Exposure at Risk", fmt_inr(exposure))
    k5.metric("% Invoices Flagged", f"{pct_flagged:.1f}%")

    st.divider()

    # ── Row 1: leakage by rule + monthly trend ────────────────────────────────
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Potential Leakage and Exposure by Rule")
        rule_data = pd.DataFrame([
            {
                "Rule":    "Duplicates",
                "Type":    "Potential Leakage",
                "Amount":  scored.loc[scored["flag_duplicate"] == 1, "potential_leakage_inr"].sum(),
            },
            {
                "Rule":    "Outliers",
                "Type":    "Potential Leakage",
                "Amount":  scored.loc[
                    (scored["flag_outlier"] == 1) & (scored["flag_duplicate"] == 0),
                    "potential_leakage_inr"
                ].sum(),
            },
            {
                "Rule":    "Split Invoices",
                "Type":    "Exposure at Risk",
                "Amount":  scored.loc[
                    (scored["flag_split_invoice"] == 1) &
                    (scored["flag_duplicate"] == 0) & (scored["flag_outlier"] == 0),
                    "exposure_at_risk_inr"
                ].sum(),
            },
            {
                "Rule":    "Round Figure",
                "Type":    "Exposure at Risk",
                "Amount":  scored.loc[
                    (scored["flag_round_figure"] == 1) &
                    (scored["flag_duplicate"] == 0) & (scored["flag_outlier"] == 0),
                    "exposure_at_risk_inr"
                ].sum(),
            },
            {
                "Rule":    "Weekend/Holiday",
                "Type":    "Exposure at Risk",
                "Amount":  scored.loc[
                    (scored["flag_weekend_holiday"] == 1) &
                    (scored["flag_duplicate"] == 0) & (scored["flag_outlier"] == 0),
                    "exposure_at_risk_inr"
                ].sum(),
            },
        ])
        fig_bar = px.bar(
            rule_data,
            x="Rule", y="Amount", color="Type",
            barmode="group",
            color_discrete_map={
                "Potential Leakage": "#d62728",
                "Exposure at Risk":  "#ff7f0e",
            },
            labels={"Amount": "Amount (Rs)"},
        )
        fig_bar.update_layout(
            margin=dict(t=20, b=20), legend_title_text="",
            yaxis_tickformat=","
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_right:
        st.subheader("Monthly Flagged Amount Trend")
        scored["year_month"] = scored["invoice_date"].dt.to_period("M")
        monthly = (
            scored[scored["flag_count"] > 0]
            .groupby("year_month")
            .agg(
                flagged_amount = ("amount_inr", "sum"),
                flagged_count  = ("invoice_id", "count"),
            )
            .reset_index()
        )
        monthly["year_month_str"] = monthly["year_month"].astype(str)
        monthly = monthly.sort_values("year_month_str")

        fig_line = px.line(
            monthly, x="year_month_str", y="flagged_amount",
            markers=True,
            labels={"year_month_str": "Month", "flagged_amount": "Flagged Amount (Rs)"},
        )
        fig_line.update_traces(line_color="#1f77b4")
        fig_line.update_layout(
            margin=dict(t=20, b=20), xaxis_tickangle=-45,
            yaxis_tickformat=","
        )
        st.plotly_chart(fig_line, use_container_width=True)

    st.divider()

    # ── Row 2: top vendors + top suspicious invoices ──────────────────────────
    col_v, col_i = st.columns([1, 1])

    with col_v:
        st.subheader("Top 10 Risky Vendors")
        top_vendors = (
            vendor.sort_values("potential_leakage_inr", ascending=False)
            .head(10)[["vendor_name", "flagged_count", "avg_risk_score",
                        "potential_leakage_inr", "exposure_at_risk_inr"]]
            .rename(columns={
                "vendor_name":           "Vendor",
                "flagged_count":         "Flagged",
                "avg_risk_score":        "Avg Risk",
                "potential_leakage_inr": "Pot. Leakage (Rs)",
                "exposure_at_risk_inr":  "Exposure (Rs)",
            })
        )
        top_vendors["Pot. Leakage (Rs)"] = top_vendors["Pot. Leakage (Rs)"].apply(fmt_inr)
        top_vendors["Exposure (Rs)"]     = top_vendors["Exposure (Rs)"].apply(fmt_inr)
        st.dataframe(top_vendors, use_container_width=True, hide_index=True)

    with col_i:
        st.subheader("Top 20 Suspicious Invoices")
        top_inv = (
            scored[scored["flag_count"] > 0]
            .sort_values("risk_score", ascending=False)
            .head(20)[["invoice_id", "vendor_name", "amount_inr",
                        "risk_score", "triggered_rules", "payment_date"]]
            .rename(columns={
                "invoice_id":      "Invoice ID",
                "vendor_name":     "Vendor",
                "amount_inr":      "Amount (Rs)",
                "risk_score":      "Score",
                "triggered_rules": "Rules",
                "payment_date":    "Payment Date",
            })
        )
        top_inv["Amount (Rs)"]   = top_inv["Amount (Rs)"].apply(fmt_inr)
        top_inv["Payment Date"]  = top_inv["Payment Date"].dt.strftime("%Y-%m-%d")
        st.dataframe(top_inv, use_container_width=True, hide_index=True)

# ════════════════════════════════════════════════════════════════════════════
# MODEL QUALITY TAB
# ════════════════════════════════════════════════════════════════════════════
with tab_quality:
    st.title("Model Quality: Per-Rule Precision, Recall, F1")

    st.info(
        "This data is **synthetic**. Anomalies were injected to match rule conditions "
        "exactly, so recall is artificially high. On real data, expect lower recall "
        "(estimated 20-40 percentage points lower) and similarly imperfect precision."
    )

    display_metrics = metrics_df.copy()
    display_metrics["precision"] = (display_metrics["precision"] * 100).round(1).astype(str) + "%"
    display_metrics["recall"]    = (display_metrics["recall"]    * 100).round(1).astype(str) + "%"
    display_metrics["f1"]        = (display_metrics["f1"]        * 100).round(1).astype(str) + "%"

    show_cols = ["rule", "flagged", "gt_count", "tp", "fp", "fn", "precision", "recall", "f1"]
    col_rename = {
        "rule":      "Rule",
        "flagged":   "Flagged",
        "gt_count":  "GT Count",
        "tp":        "TP",
        "fp":        "FP",
        "fn":        "FN",
        "precision": "Precision",
        "recall":    "Recall",
        "f1":        "F1",
    }
    st.dataframe(
        display_metrics[show_cols].rename(columns=col_rename),
        use_container_width=True,
        hide_index=True,
    )

    # Precision / Recall bar chart
    fig_pr = go.Figure()
    fig_pr.add_trace(go.Bar(
        name="Precision",
        x=metrics_df["rule"],
        y=(metrics_df["precision"] * 100).round(1),
        marker_color="#2196F3",
    ))
    fig_pr.add_trace(go.Bar(
        name="Recall",
        x=metrics_df["rule"],
        y=(metrics_df["recall"] * 100).round(1),
        marker_color="#4CAF50",
    ))
    fig_pr.add_trace(go.Bar(
        name="F1",
        x=metrics_df["rule"],
        y=(metrics_df["f1"] * 100).round(1),
        marker_color="#FF9800",
    ))
    fig_pr.update_layout(
        barmode="group",
        yaxis_title="Score (%)",
        yaxis_range=[0, 110],
        legend_title_text="Metric",
        margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig_pr, use_container_width=True)

    st.caption(
        "See outputs/evaluation_report.md for a detailed per-rule explanation of "
        "false positives and false negatives."
    )
