"""
score.py
========
Runs all 5 SQL detection rules, combines flags per invoice, computes a
risk score, classifies risk level, and builds vendor-level risk summaries.

Outputs (written to /outputs)
------------------------------
  scored_invoices.csv   -- one row per invoice with flag columns + risk score
  vendor_risk.csv       -- one row per vendor with aggregate risk metrics

Usage
-----
  python src/score.py          (run from project root)

Scoring logic
-------------
  risk_score = sum of weights for every rule that fired on that invoice,
               capped at 100.
  risk_level:
    HIGH   if risk_score >= high_risk_threshold   (default 40)
    MEDIUM if risk_score >= medium_risk_threshold  (default 20)
    LOW    otherwise

Leakage definitions (from MY_DESIGN.md)
----------------------------------------
  potential_leakage_inr   (formerly "confirmed leakage"):
    - For duplicate-flagged invoices: full invoice amount.
    - For outlier-flagged invoices (not also duplicate): excess over that
      vendor's mean amount (only when positive).
    - If both duplicate and outlier fire on the same invoice: count only
      the duplicate amount (avoid double-counting).
    - NOTE: includes FP-flagged invoices; auditor review would reduce this.

  exposure_at_risk_inr:
    - Full invoice amount for invoices flagged ONLY by split_invoice,
      round_figure, or weekend_holiday -- i.e. not flagged by duplicate
      or outlier.
    - No overlap with potential_leakage_inr by construction.
"""

import sqlite3
import yaml
import pandas as pd
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.parent
DB_PATH      = ROOT / "data" / "db" / "invoices.db"
CONFIG_PATH  = ROOT / "config.yaml"
SQL_DIR      = ROOT / "sql"
OUTPUTS_DIR  = ROOT / "outputs"

SCORED_CSV   = OUTPUTS_DIR / "scored_invoices.csv"
VENDOR_CSV   = OUTPUTS_DIR / "vendor_risk.csv"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def run_rule(conn: sqlite3.Connection, sql_file: str, params: dict) -> set:
    """Execute one SQL rule file; return set of flagged invoice_ids."""
    sql = (SQL_DIR / sql_file).read_text(encoding="utf-8")
    cur = conn.execute(sql, params)
    return {row[0] for row in cur.fetchall()}


def get_vendor_means(conn: sqlite3.Connection) -> dict:
    """Return {vendor_id: mean_amount_inr} from the full invoice table."""
    rows = conn.execute(
        "SELECT vendor_id, AVG(amount_inr) FROM invoices GROUP BY vendor_id"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def build_scored_invoices(conn: sqlite3.Connection, cfg: dict) -> pd.DataFrame:
    """
    1. Load all invoices.
    2. Run each rule and record which invoices are flagged.
    3. Compute risk_score and risk_level.
    4. Compute potential_leakage_inr and exposure_at_risk_inr per invoice.
    """
    weights = cfg["rule_weights"]
    high_t  = cfg["high_risk_threshold"]
    med_t   = cfg["medium_risk_threshold"]
    al      = cfg["approval_limit"]

    inv = pd.read_sql_query("SELECT * FROM invoices", conn)

    flag_dup   = run_rule(conn, "01_duplicates.sql",      {})
    flag_split = run_rule(conn, "02_split_invoices.sql",  {"approval_limit": al})
    flag_wknd  = run_rule(conn, "03_weekend_holiday.sql", {})
    flag_rnd   = run_rule(conn, "04_round_figure.sql",    {})
    flag_out   = run_rule(conn, "05_outliers.sql",        {})

    inv["flag_duplicate"]       = inv["invoice_id"].isin(flag_dup).astype(int)
    inv["flag_split_invoice"]   = inv["invoice_id"].isin(flag_split).astype(int)
    inv["flag_weekend_holiday"] = inv["invoice_id"].isin(flag_wknd).astype(int)
    inv["flag_round_figure"]    = inv["invoice_id"].isin(flag_rnd).astype(int)
    inv["flag_outlier"]         = inv["invoice_id"].isin(flag_out).astype(int)

    inv["flag_count"] = (
        inv["flag_duplicate"] + inv["flag_split_invoice"] +
        inv["flag_weekend_holiday"] + inv["flag_round_figure"] +
        inv["flag_outlier"]
    )

    # Risk score: sum of triggered weights, capped at 100
    inv["risk_score"] = (
        inv["flag_duplicate"]       * weights["duplicate"]       +
        inv["flag_split_invoice"]   * weights["split_invoice"]   +
        inv["flag_weekend_holiday"] * weights["weekend_holiday"] +
        inv["flag_round_figure"]    * weights["round_figure"]    +
        inv["flag_outlier"]         * weights["outlier"]
    ).clip(upper=100)

    inv["risk_level"] = inv["risk_score"].apply(
        lambda s: "HIGH" if s >= high_t else ("MEDIUM" if s >= med_t else "LOW")
    )

    vendor_means = get_vendor_means(conn)
    inv["_vendor_mean"] = inv["vendor_id"].map(vendor_means)

    # ── Potential leakage (flagged) ───────────────────────────────────────────
    # Duplicate takes priority over outlier if both fire on the same invoice.
    potential = []
    for _, row in inv.iterrows():
        if row["flag_duplicate"] == 1:
            potential.append(row["amount_inr"])
        elif row["flag_outlier"] == 1:
            excess = row["amount_inr"] - row["_vendor_mean"]
            potential.append(max(excess, 0.0))
        else:
            potential.append(0.0)
    inv["potential_leakage_inr"] = potential

    # ── Exposure at risk ──────────────────────────────────────────────────────
    # Invoices flagged ONLY by split / round / weekend (not dup or outlier).
    exposure = []
    for _, row in inv.iterrows():
        if row["flag_duplicate"] == 1 or row["flag_outlier"] == 1:
            exposure.append(0.0)
        elif (row["flag_split_invoice"]   == 1 or
              row["flag_round_figure"]    == 1 or
              row["flag_weekend_holiday"] == 1):
            exposure.append(row["amount_inr"])
        else:
            exposure.append(0.0)
    inv["exposure_at_risk_inr"] = exposure

    inv = inv.drop(columns=["_vendor_mean"])
    return inv


def build_vendor_risk(scored: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to vendor level. Totals must reconcile with invoice level."""
    vendor_grp = scored.groupby(["vendor_id", "vendor_name"]).agg(
        invoice_count         = ("invoice_id",            "count"),
        flagged_count         = ("flag_count",             lambda x: (x > 0).sum()),
        total_spend_inr       = ("amount_inr",             "sum"),
        flagged_amount_inr    = ("amount_inr",             lambda x: x[scored.loc[x.index, "flag_count"] > 0].sum()),
        potential_leakage_inr = ("potential_leakage_inr",  "sum"),
        exposure_at_risk_inr  = ("exposure_at_risk_inr",   "sum"),
        avg_risk_score        = ("risk_score",             "mean"),
        max_risk_score        = ("risk_score",             "max"),
    ).reset_index()

    vendor_grp["flag_rate_pct"]  = (
        vendor_grp["flagged_count"] / vendor_grp["invoice_count"] * 100
    ).round(2)
    vendor_grp["avg_risk_score"] = vendor_grp["avg_risk_score"].round(2)
    vendor_grp["risk_tier"] = vendor_grp["max_risk_score"].apply(
        lambda s: "HIGH" if s >= 40 else ("MEDIUM" if s >= 20 else "LOW")
    )

    return vendor_grp.sort_values("potential_leakage_inr", ascending=False)


def run_self_checks(scored: pd.DataFrame, vendor: pd.DataFrame) -> list:
    """All self-checks. Raises AssertionError on any failure."""
    results = []

    results.append(f"[CHECK 1] scored_invoices row count: {len(scored):,}")

    dup_ids = scored["invoice_id"].duplicated().sum()
    assert dup_ids == 0, f"FAIL: {dup_ids} duplicate invoice_ids"
    results.append("[CHECK 2] No duplicate invoice_ids: PASS")

    max_score = scored["risk_score"].max()
    assert max_score <= 100, f"FAIL: max risk_score = {max_score}"
    results.append(f"[CHECK 3] Max risk_score = {max_score} (<= 100): PASS")

    high_ok = ((scored["risk_level"] == "HIGH")   == (scored["risk_score"] >= 40)).all()
    med_ok  = ((scored["risk_level"] == "MEDIUM") == ((scored["risk_score"] >= 20) & (scored["risk_score"] < 40))).all()
    low_ok  = ((scored["risk_level"] == "LOW")    == (scored["risk_score"] < 20)).all()
    assert high_ok and med_ok and low_ok, "FAIL: risk levels do not match thresholds"
    results.append("[CHECK 4] Risk level assignments match thresholds: PASS")

    overlap = scored[(scored["potential_leakage_inr"] > 0) &
                     (scored["exposure_at_risk_inr"]  > 0)]
    assert len(overlap) == 0, f"FAIL: {len(overlap)} invoices in both potential and exposure"
    results.append("[CHECK 5] No overlap between potential_leakage and exposure_at_risk: PASS")

    inv_pot = scored["potential_leakage_inr"].sum()
    vnd_pot = vendor["potential_leakage_inr"].sum()
    inv_exp = scored["exposure_at_risk_inr"].sum()
    vnd_exp = vendor["exposure_at_risk_inr"].sum()
    assert abs(inv_pot - vnd_pot) < 0.01, \
        f"FAIL: potential leakage mismatch: invoice={inv_pot:.2f} vendor={vnd_pot:.2f}"
    assert abs(inv_exp - vnd_exp) < 0.01, \
        f"FAIL: exposure mismatch: invoice={inv_exp:.2f} vendor={vnd_exp:.2f}"
    results.append("[CHECK 6] Vendor totals reconcile with invoice totals: PASS")

    return results


def main():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg  = load_config()
    conn = sqlite3.connect(DB_PATH)

    print("Running 5 SQL rules and scoring invoices...")
    scored = build_scored_invoices(conn, cfg)

    print("Building vendor risk table...")
    vendor = build_vendor_risk(scored)

    print("Running self-checks...")
    for r in run_self_checks(scored, vendor):
        print(f"  {r}")

    scored.to_csv(SCORED_CSV, index=False)
    vendor.to_csv(VENDOR_CSV, index=False)
    print(f"\nOutputs written:")
    print(f"  {SCORED_CSV}  ({len(scored):,} rows)")
    print(f"  {VENDOR_CSV}  ({len(vendor):,} rows)")

    total    = len(scored)
    flagged  = (scored["flag_count"] > 0).sum()
    high_ct  = (scored["risk_level"] == "HIGH").sum()
    med_ct   = (scored["risk_level"] == "MEDIUM").sum()
    low_ct   = (scored["risk_level"] == "LOW").sum()
    potential = scored["potential_leakage_inr"].sum()
    exposure  = scored["exposure_at_risk_inr"].sum()

    print("\n" + "=" * 65)
    print("SCORE.PY SUMMARY")
    print("=" * 65)
    print(f"Total invoices scored         : {total:,}")
    print(f"Flagged (any rule)            : {flagged:,}  ({flagged/total*100:.1f}%)")
    print()
    print(f"Risk level breakdown:")
    print(f"  HIGH   (score >= 40)        : {high_ct:,}")
    print(f"  MEDIUM (score 20-39)        : {med_ct:,}")
    print(f"  LOW    (score <  20)        : {low_ct:,}")
    print()
    print(f"Potential leakage (flagged)   : Rs {potential:>15,.2f}")
    print(f"Exposure at risk              : Rs {exposure:>15,.2f}")
    print(f"Total financial exposure      : Rs {potential+exposure:>15,.2f}")
    print()
    print("Per-rule flag counts:")
    for col, label in [
        ("flag_duplicate",       "Duplicates      "),
        ("flag_split_invoice",   "Split invoices  "),
        ("flag_weekend_holiday", "Weekend/holiday "),
        ("flag_round_figure",    "Round figure    "),
        ("flag_outlier",         "Outliers        "),
    ]:
        print(f"  {label}: {scored[col].sum():,}")
    print("=" * 65)

    conn.close()
    return scored, vendor


if __name__ == "__main__":
    main()
