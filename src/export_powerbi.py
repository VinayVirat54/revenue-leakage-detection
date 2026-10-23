"""
export_powerbi.py
=================
Exports clean CSVs to /powerbi_export in star-schema form for Power BI Desktop.

Tables written
--------------
  fact_invoices.csv  -- fact table; one row per invoice
  dim_vendor.csv     -- vendor dimension
  dim_date.csv       -- date dimension (every calendar date in the data range)
  rule_summary.csv   -- per-rule metrics summary

Usage
-----
  python src/export_powerbi.py          (run from project root)
"""

import pandas as pd
from pathlib import Path

ROOT          = Path(__file__).parent.parent
SCORED_CSV    = ROOT / "outputs" / "scored_invoices.csv"
METRICS_CSV   = ROOT / "outputs" / "evaluation_metrics.csv"
EXPORT_DIR    = ROOT / "powerbi_export"


def build_fact_invoices(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Fact table: one row per invoice.
    Columns: invoice_id, vendor_id, date_key, amount_inr, risk_score,
             risk_level, flag_duplicate, flag_split_invoice,
             flag_weekend_holiday, flag_round_figure, flag_outlier,
             flag_count, potential_leakage_inr, exposure_at_risk_inr.
    date_key = integer YYYYMMDD for joining to dim_date.
    """
    fact = scored[[
        "invoice_id", "vendor_id", "invoice_date", "amount_inr",
        "risk_score", "risk_level",
        "flag_duplicate", "flag_split_invoice", "flag_weekend_holiday",
        "flag_round_figure", "flag_outlier", "flag_count",
        "potential_leakage_inr", "exposure_at_risk_inr",
    ]].copy()

    fact["invoice_date"] = pd.to_datetime(fact["invoice_date"])
    fact["date_key"] = fact["invoice_date"].dt.strftime("%Y%m%d").astype(int)
    fact = fact.drop(columns=["invoice_date"])

    return fact


def build_dim_vendor(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Vendor dimension: one row per vendor.
    """
    dim = (
        scored[["vendor_id", "vendor_name", "department"]]
        .drop_duplicates(subset=["vendor_id"])
        .sort_values("vendor_id")
        .reset_index(drop=True)
    )
    return dim


def build_dim_date(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Date dimension: one row per calendar date covering the full invoice range.
    Columns: date_key, date, year, month, month_name, quarter,
             financial_year (Indian FY: Apr-Mar), is_weekend.
    """
    min_dt = pd.to_datetime(scored["invoice_date"]).min()
    max_dt = pd.to_datetime(scored["invoice_date"]).max()

    all_dates = pd.date_range(min_dt, max_dt, freq="D")
    dim = pd.DataFrame({"date": all_dates})

    dim["date_key"]       = dim["date"].dt.strftime("%Y%m%d").astype(int)
    dim["year"]           = dim["date"].dt.year
    dim["month"]          = dim["date"].dt.month
    dim["month_name"]     = dim["date"].dt.strftime("%B")
    dim["quarter"]        = dim["date"].dt.quarter
    dim["is_weekend"]     = (dim["date"].dt.dayofweek >= 5).astype(int)

    # Indian financial year: Apr-Mar
    # e.g. date 2022-05-15 -> FY 2022-23
    def fy(row):
        y = row["year"]
        m = row["month"]
        if m >= 4:
            return f"FY {y}-{str(y+1)[-2:]}"
        else:
            return f"FY {y-1}-{str(y)[-2:]}"

    dim["financial_year"] = dim.apply(fy, axis=1)
    dim["date"] = dim["date"].dt.strftime("%Y-%m-%d")

    return dim[["date_key", "date", "year", "month", "month_name",
                "quarter", "financial_year", "is_weekend"]]


def build_rule_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    """
    Per-rule summary for Power BI.
    """
    summary = metrics[[
        "rule", "gt_type", "flagged", "gt_count",
        "tp", "fp", "fn", "precision", "recall", "f1"
    ]].copy()
    summary["precision_pct"] = (summary["precision"] * 100).round(1)
    summary["recall_pct"]    = (summary["recall"]    * 100).round(1)
    summary["f1_pct"]        = (summary["f1"]        * 100).round(1)
    return summary


def run_self_checks(fact: pd.DataFrame,
                    dim_vendor: pd.DataFrame,
                    dim_date: pd.DataFrame) -> list:
    results = []

    # 1. Row counts match
    assert len(fact) == len(pd.read_csv(SCORED_CSV)), \
        "FAIL: fact_invoices row count mismatch"
    results.append(f"[CHECK 1] fact_invoices row count = {len(fact):,}: PASS")

    # 2. All vendor_ids in fact exist in dim_vendor
    fact_vendors = set(fact["vendor_id"])
    dim_vendors  = set(dim_vendor["vendor_id"])
    missing_v = fact_vendors - dim_vendors
    assert len(missing_v) == 0, f"FAIL: {len(missing_v)} vendor_ids not in dim_vendor"
    results.append("[CHECK 2] All vendor_ids in dim_vendor: PASS")

    # 3. All date_keys in fact exist in dim_date
    fact_keys = set(fact["date_key"])
    dim_keys  = set(dim_date["date_key"])
    missing_d = fact_keys - dim_keys
    assert len(missing_d) == 0, f"FAIL: {len(missing_d)} date_keys not in dim_date"
    results.append("[CHECK 3] All date_keys in dim_date: PASS")

    # 4. No duplicate invoice_ids in fact
    dup = fact["invoice_id"].duplicated().sum()
    assert dup == 0, f"FAIL: {dup} duplicate invoice_ids in fact"
    results.append("[CHECK 4] No duplicate invoice_ids in fact: PASS")

    return results


def main():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    scored  = pd.read_csv(SCORED_CSV)
    metrics = pd.read_csv(METRICS_CSV)

    print("Building star-schema tables...")
    fact       = build_fact_invoices(scored)
    dim_vendor = build_dim_vendor(scored)
    dim_date   = build_dim_date(scored)
    rule_sum   = build_rule_summary(metrics)

    print("Running self-checks...")
    for r in run_self_checks(fact, dim_vendor, dim_date):
        print(f"  {r}")

    # Write CSVs
    fact.to_csv(       EXPORT_DIR / "fact_invoices.csv", index=False)
    dim_vendor.to_csv( EXPORT_DIR / "dim_vendor.csv",    index=False)
    dim_date.to_csv(   EXPORT_DIR / "dim_date.csv",      index=False)
    rule_sum.to_csv(   EXPORT_DIR / "rule_summary.csv",  index=False)

    print(f"\nFiles written to {EXPORT_DIR}:")
    for f in sorted(EXPORT_DIR.glob("*.csv")):
        rows = sum(1 for _ in open(f)) - 1
        print(f"  {f.name:<30} {rows:,} rows")


if __name__ == "__main__":
    main()
