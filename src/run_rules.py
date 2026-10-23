"""
run_rules.py
============
Executes all 5 SQL detection rules against the SQLite database and reports:
  1. How many invoices each rule flagged.
  2. Quick sanity check: of those flagged invoices, how many are in
     data/ground_truth.csv under the matching anomaly_type.
     (This is NOT the full precision/recall evaluation -- that is Stage C.)

Outputs a summary to stdout only.  Does not write any files.

Usage
-----
  python src/run_rules.py          (run from project root)

Dependencies
------------
  - data/db/invoices.db must exist (run src/load_db.py first)
  - data/ground_truth.csv must exist (run src/generate_data.py first)
  - config.yaml must exist at project root
"""

import sqlite3
import yaml
import pandas as pd
from pathlib import Path

# ── Paths: always relative to project root ───────────────────────────────────
ROOT             = Path(__file__).parent.parent
DB_PATH          = ROOT / "data" / "db" / "invoices.db"
GROUND_TRUTH_CSV = ROOT / "data" / "ground_truth.csv"
CONFIG_YAML      = ROOT / "config.yaml"
SQL_DIR          = ROOT / "sql"

# ── Map SQL file -> ground_truth anomaly_type for sanity check ───────────────
RULE_META = [
    {
        "file":        "01_duplicates.sql",
        "name":        "Rule 1: Duplicates",
        "gt_type":     "duplicate",
        "param_names": [],          # no extra params needed
    },
    {
        "file":        "02_split_invoices.sql",
        "name":        "Rule 2: Split Invoices",
        "gt_type":     "split_invoice",
        "param_names": ["approval_limit"],
    },
    {
        "file":        "03_weekend_holiday.sql",
        "name":        "Rule 3: Weekend/Holiday",
        "gt_type":     "weekend_holiday",
        "param_names": [],
    },
    {
        "file":        "04_round_figure.sql",
        "name":        "Rule 4: Round Figure",
        "gt_type":     "round_figure",
        "param_names": [],
    },
    {
        "file":        "05_outliers.sql",
        "name":        "Rule 5: Outliers",
        "gt_type":     "outlier",
        "param_names": [],
    },
]


def load_config() -> dict:
    """Read config.yaml from project root and return as a dict."""
    with open(CONFIG_YAML, "r") as f:
        return yaml.safe_load(f)


def run_rule(conn: sqlite3.Connection,
             sql: str,
             params: dict) -> pd.DataFrame:
    """
    Execute a SQL rule with named parameters (:param_name syntax).
    Returns a DataFrame with at least columns: invoice_id, reason.
    """
    # sqlite3 uses the dict directly for named params
    cursor = conn.execute(sql, params)
    cols   = [desc[0] for desc in cursor.description]
    rows   = cursor.fetchall()
    return pd.DataFrame(rows, columns=cols)


def sanity_check(flagged_ids: set,
                 gt_df: pd.DataFrame,
                 gt_type: str) -> tuple[int, int]:
    """
    Quick overlap check:
      - true_hits  : flagged invoices that ARE in ground_truth for this rule's type
      - injected   : total invoices injected for this rule's type (from ground_truth)
    Returns (true_hits, injected).
    """
    gt_for_type = set(gt_df.loc[gt_df["anomaly_type"] == gt_type, "invoice_id"])
    true_hits   = len(flagged_ids & gt_for_type)
    return true_hits, len(gt_for_type)


def main():
    # ── Load config ───────────────────────────────────────────────────────────
    cfg            = load_config()
    approval_limit = cfg["approval_limit"]

    # ── Load ground truth for sanity check ───────────────────────────────────
    gt_df = pd.read_csv(GROUND_TRUTH_CSV)

    # ── Connect to SQLite ─────────────────────────────────────────────────────
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. "
            "Run 'python src/load_db.py' first."
        )
    conn = sqlite3.connect(DB_PATH)

    # ── Run each rule ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RUN_RULES — STAGE B DETECTION SUMMARY")
    print(f"  approval_limit from config.yaml: Rs {approval_limit:,}")
    print("=" * 70)

    all_results = {}   # rule name -> DataFrame of flagged invoices

    for meta in RULE_META:
        sql_path = SQL_DIR / meta["file"]
        sql      = sql_path.read_text(encoding="utf-8")

        # Build named params dict (only approval_limit used currently)
        params = {}
        if "approval_limit" in meta["param_names"]:
            params["approval_limit"] = approval_limit

        flagged_df = run_rule(conn, sql, params)
        flagged_ids = set(flagged_df["invoice_id"])

        true_hits, injected = sanity_check(flagged_ids, gt_df, meta["gt_type"])

        # Store for summary
        all_results[meta["name"]] = flagged_df

        print(f"\n  {meta['name']}")
        print(f"    SQL file          : {meta['file']}")
        print(f"    Invoices flagged  : {len(flagged_ids):,}")
        print(f"    Injected (GT)     : {injected}")
        print(f"    True hits (sanity): {true_hits}  "
              f"({true_hits/injected*100:.1f}% of injected found)" if injected > 0
              else f"    True hits (sanity): {true_hits}")
        if len(flagged_df) > 0:
            print(f"    Sample reason     : {flagged_df['reason'].iloc[0]}")

    # ── Overall summary ───────────────────────────────────────────────────────
    total_invoices  = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    all_flagged_ids = set()
    for df in all_results.values():
        all_flagged_ids |= set(df["invoice_id"])

    print("\n" + "-" * 70)
    print("OVERALL")
    print(f"  Total invoices in DB      : {total_invoices:,}")
    print(f"  Unique invoices flagged   : {len(all_flagged_ids):,}  "
          f"({len(all_flagged_ids)/total_invoices*100:.2f}% of total)")
    total_gt = len(gt_df)
    total_hits = len(all_flagged_ids & set(gt_df["invoice_id"]))
    print(f"  Total injected anomalies  : {total_gt}")
    print(f"  Injected found (any rule) : {total_hits}  "
          f"({total_hits/total_gt*100:.1f}% of injected)")
    print("=" * 70)
    print("(Full precision/recall/F1 evaluation is in Stage C)")

    conn.close()


if __name__ == "__main__":
    main()
