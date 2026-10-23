"""
generate_data.py
================
Generates ~20,000 synthetic vendor invoices for 150 vendors over 3 Indian
financial years (2022-04-01 to 2025-03-31).

Outputs
-------
  data/raw/invoices.csv       — all invoices (normal + anomalous)
  data/ground_truth.csv       — one row per anomalous invoice
  data/holidays.csv           — must already exist (hardcoded Indian holidays)

Usage
-----
  python src/generate_data.py          (run from project root)

Design notes
------------
  - seed=42 everywhere → fully reproducible; two runs produce identical files
  - Each vendor has its own lognormal amount distribution (different sizes)
  - Normal payments: always snapped to a weekday first, then 2% are
    intentionally moved to a weekend (coin-flip AFTER snap) so the weekend
    rule is not trivially perfect
  - ~10% of vendors are 'retainer' vendors; 50% of their invoices are
    naturally round-figure, giving the round-figure rule false positives
  - Anomalies ~0.8% per type (≈160 clusters/invoices) → total ~4% rows
  - ground_truth has ONE ROW PER ANOMALOUS INVOICE with group_id column
"""

import random
import hashlib
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import timedelta

# ── Seed everything for reproducibility ──────────────────────────────────────
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# ── Paths: always relative to project root (one level up from src/) ──────────
ROOT              = Path(__file__).parent.parent   # project root
RAW_DIR           = ROOT / "data" / "raw"
DATA_DIR          = ROOT / "data"
INVOICES_PATH     = RAW_DIR / "invoices.csv"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.csv"
HOLIDAYS_PATH     = DATA_DIR / "holidays.csv"

# ── Configuration constants ───────────────────────────────────────────────────
N_NORMAL        = 20_000          # base normal invoices before anomaly injection
N_VENDORS       = 150
APPROVAL_LIMIT  = 50_000          # INR — split invoices stay just below this
START_DATE      = pd.Timestamp("2022-04-01")
END_DATE        = pd.Timestamp("2025-03-31")
ANOMALY_RATE    = 0.008           # ~0.8% of N_NORMAL per anomaly type ≈ 160

DEPARTMENTS = [
    "Finance", "IT", "Operations", "HR", "Marketing",
    "Procurement", "Legal", "Facilities", "R&D", "Admin",
]
PAYMENT_MODES        = ["NEFT", "RTGS", "UPI", "Cheque"]
PAYMENT_MODE_WEIGHTS = [0.45, 0.25, 0.20, 0.10]   # NEFT most common


# ── Load holiday set ──────────────────────────────────────────────────────────
def load_holidays() -> set:
    """Return a set of holiday dates as pandas Timestamps (date-only)."""
    hdf = pd.read_csv(HOLIDAYS_PATH, parse_dates=["date"])
    return set(hdf["date"].dt.normalize())


# ── Vendor catalogue ──────────────────────────────────────────────────────────
def build_vendor_catalogue(rng: np.random.Generator) -> pd.DataFrame:
    """
    Create 150 vendors. Each vendor gets:
      - log_mean / log_std : lognormal params so vendors differ in typical size
      - is_retainer        : True for ~10%; retainer vendors naturally issue
                             round-figure amounts (rent, maintenance contracts)
      - home_dept          : the department that mostly uses this vendor
    """
    vendor_ids   = [f"VND{i:04d}" for i in range(1, N_VENDORS + 1)]
    vendor_names = [f"Vendor_{i:04d}_Ltd" for i in range(1, N_VENDORS + 1)]

    # Log-space means → typical amounts range from Rs 5,000 to Rs 5,00,000
    log_means   = rng.uniform(np.log(5_000), np.log(5_00_000), N_VENDORS)
    log_stds    = rng.uniform(0.3, 0.8, N_VENDORS)
    is_retainer = rng.random(N_VENDORS) < 0.10    # ~10% retainer vendors
    home_dept   = rng.choice(DEPARTMENTS, N_VENDORS)

    return pd.DataFrame({
        "vendor_id":   vendor_ids,
        "vendor_name": vendor_names,
        "log_mean":    log_means,
        "log_std":     log_stds,
        "is_retainer": is_retainer,
        "home_dept":   home_dept,
    })


# ── Helper: next weekday ──────────────────────────────────────────────────────
def to_weekday(date: pd.Timestamp) -> pd.Timestamp:
    """Shift forward until Mon–Fri."""
    while date.weekday() >= 5:   # 5=Sat, 6=Sun
        date += timedelta(days=1)
    return date


# ── Helper: to_weekend ───────────────────────────────────────────────────────
def to_weekend(date: pd.Timestamp, rng: np.random.Generator) -> pd.Timestamp:
    """
    Move date to the nearest upcoming Saturday (0 offset) or Sunday (1 offset).
    Coin-flip which one.
    """
    days_to_sat = (5 - date.weekday()) % 7   # days until next Saturday
    if days_to_sat == 0:
        days_to_sat = 7                        # already Saturday → next one
    days_to_sun = days_to_sat + 1

    offset = int(rng.choice([days_to_sat, days_to_sun]))
    return date + timedelta(days=offset)


# ── Helper: payment date for normal invoices ──────────────────────────────────
def payment_date_normal(invoice_date: pd.Timestamp,
                        rng: np.random.Generator) -> pd.Timestamp:
    """
    Normal payment: invoice_date + 15–45 days.

    Always snap to weekday first, THEN 2% coin-flip to move to a weekend.
    This ensures ~2% of normal payments fall on weekends (realistic noise)
    while the other 98% are genuine weekday payments.
    """
    delta  = int(rng.integers(15, 46))              # 15 to 45 days inclusive
    pay_dt = invoice_date + timedelta(days=delta)
    pay_dt = to_weekday(pay_dt)                     # step 1: force weekday

    if rng.random() < 0.02:                         # step 2: 2% → move to weekend
        pay_dt = to_weekend(pay_dt, rng)

    return pay_dt


# ── Helper: sample invoice amount for a vendor ────────────────────────────────
def sample_amount(vendor_row: pd.Series,
                  rng: np.random.Generator,
                  force_round: bool = False) -> float:
    """
    Draw from the vendor's lognormal distribution.
    If force_round (retainer vendor), round to nearest 10,000.
    """
    raw = float(np.exp(rng.normal(vendor_row["log_mean"], vendor_row["log_std"])))
    raw = max(raw, 500.0)   # floor at Rs 500
    raw = round(raw, 2)

    if force_round:
        raw = round(raw / 10_000) * 10_000
        raw = max(raw, 10_000)

    return raw


# ── Generate normal invoices ──────────────────────────────────────────────────
def generate_normal(vendors: pd.DataFrame,
                    rng: np.random.Generator) -> pd.DataFrame:
    """
    Generate N_NORMAL invoices. These are 'clean' — no anomalies.
    A temp column '_orig_idx' is added so anomaly injectors can reference
    the original row when building group_ids; it is stripped before saving.
    """
    total_days   = (END_DATE - START_DATE).days
    approver_ids = [f"APR{i:03d}" for i in range(1, 21)]
    records      = []

    for i in range(N_NORMAL):
        v_idx  = int(rng.integers(0, N_VENDORS))
        vendor = vendors.iloc[v_idx]

        inv_date = START_DATE + timedelta(days=int(rng.integers(0, total_days)))
        pay_date = payment_date_normal(inv_date, rng)

        # Retainer vendors: 50% of their invoices are naturally round-figure
        force_round = bool(vendor["is_retainer"] and rng.random() < 0.50)
        amount      = sample_amount(vendor, rng, force_round=force_round)

        dept = vendor["home_dept"] if rng.random() < 0.80 else rng.choice(DEPARTMENTS)

        records.append({
            "_orig_idx":   i,                         # temp: for group_id linking
            "vendor_id":   vendor["vendor_id"],
            "vendor_name": vendor["vendor_name"],
            "invoice_date": inv_date.date(),
            "payment_date": pay_date.date(),
            "amount_inr":   amount,
            "approver_id":  rng.choice(approver_ids),
            "department":   dept,
            "payment_mode": rng.choice(PAYMENT_MODES, p=PAYMENT_MODE_WEIGHTS),
            "_anomaly_type": None,                    # temp: filled by injectors
        })

    return pd.DataFrame(records)


# ══════════════════════════════════════════════════════════════════════════════
# Anomaly injectors
# Each returns:
#   injected_rows  — list of dicts to append to the invoice pool
#   truth_rows     — list of dicts for ground_truth (one per injected invoice)
#
# Every truth_row has:
#   anomaly_type, group_id, injected_extra_amount
# These are matched to invoice_ids AFTER shuffle via a positional tag
# (_tag) embedded in every injected row.
# ══════════════════════════════════════════════════════════════════════════════

def inject_duplicates(normal_df: pd.DataFrame,
                      rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 1 — Duplicate payments.
    For each of ~160 normal invoices, create ONE duplicate invoice:
      - same vendor_id, same amount_inr, same approver, same department
      - payment_date 1–7 days after the original payment_date
    Labels (ground_truth):
      - Only the NEW (later) invoice is labelled — NOT the original
      - group_id = original invoice's _orig_idx (links duplicate → original)
      - injected_extra_amount = full amount of the duplicate invoice
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    sample = normal_df.sample(n=n, random_state=SEED)
    injected, truth = [], []

    for _, row in sample.iterrows():
        orig_pay = pd.Timestamp(row["payment_date"])
        delta    = int(rng.integers(1, 8))             # 1–7 days later
        new_pay  = (orig_pay + timedelta(days=delta)).date()
        tag      = f"dup_{row['_orig_idx']}"           # unique positional tag

        injected.append({
            "_orig_idx":    None,
            "_tag":         tag,                       # links to truth_row
            "vendor_id":    row["vendor_id"],
            "vendor_name":  row["vendor_name"],
            "invoice_date": row["invoice_date"],
            "payment_date": new_pay,
            "amount_inr":   row["amount_inr"],
            "approver_id":  row["approver_id"],
            "department":   row["department"],
            "payment_mode": row["payment_mode"],
            "_anomaly_type": "duplicate",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "duplicate",
            "group_id":              str(row["_orig_idx"]),  # original row index
            "injected_extra_amount": row["amount_inr"],      # full duplicate amount
        })

    return injected, truth


def inject_split_invoices(normal_df: pd.DataFrame,
                          vendors: pd.DataFrame,
                          rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 2 — Split invoices (approval-limit bypass).
    Create ~160 clusters of 2–4 invoices each:
      - Same vendor + same approver, all within 3 days of each other
      - Each invoice amount = 80%–98% of APPROVAL_LIMIT (Rs 40,000–49,000)
      - Together they exceed APPROVAL_LIMIT
    Labels (ground_truth):
      - EVERY invoice in every cluster gets its own truth row
      - group_id = cluster index (so all invoices in a cluster share a group_id)
      - injected_extra_amount: for each invoice = its own amount
        (the entire cluster is suspicious; total excess is tracked at cluster level)
    """
    n_clusters   = int(N_NORMAL * ANOMALY_RATE)
    approver_ids = [f"APR{i:03d}" for i in range(1, 21)]
    total_days   = (END_DATE - START_DATE).days
    injected, truth = [], []

    for cluster_idx in range(n_clusters):
        v_idx        = int(rng.integers(0, N_VENDORS))
        vendor       = vendors.iloc[v_idx]
        cluster_size = int(rng.integers(2, 5))          # 2, 3, or 4 invoices
        base_date    = START_DATE + timedelta(days=int(rng.integers(0, total_days - 5)))
        approver     = rng.choice(approver_ids)
        department   = vendor["home_dept"]
        group_id     = f"split_cluster_{cluster_idx:04d}"

        for j in range(cluster_size):
            pct      = rng.uniform(0.80, 0.98)
            amount   = round(APPROVAL_LIMIT * pct, 2)
            inv_date = (base_date + timedelta(days=int(rng.integers(0, 4)))).date()
            pay_date = payment_date_normal(pd.Timestamp(inv_date), rng).date()
            tag      = f"split_{cluster_idx}_{j}"

            injected.append({
                "_orig_idx":    None,
                "_tag":         tag,
                "vendor_id":    vendor["vendor_id"],
                "vendor_name":  vendor["vendor_name"],
                "invoice_date": inv_date,
                "payment_date": pay_date,
                "amount_inr":   amount,
                "approver_id":  approver,
                "department":   department,
                "payment_mode": rng.choice(PAYMENT_MODES, p=PAYMENT_MODE_WEIGHTS),
                "_anomaly_type": "split_invoice",
            })
            truth.append({
                "_tag":                  tag,
                "anomaly_type":          "split_invoice",
                "group_id":              group_id,
                "injected_extra_amount": amount,   # individual invoice amount
            })

    return injected, truth


def inject_weekend_payments(normal_df: pd.DataFrame,
                            holidays: set,
                            rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 3 — Weekend / holiday payments.
    Copy ~160 normal invoices; force payment_date to a Sat, Sun, or holiday.
    Labels: one row per injected invoice, group_id blank, extra_amount = 0.
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    sample = normal_df.sample(n=n, random_state=SEED + 1)
    injected, truth = [], []

    # Pool of all weekend + holiday dates in the range
    all_dates            = pd.date_range(START_DATE, END_DATE, freq="D")
    weekend_holiday_pool = [
        d for d in all_dates
        if d.weekday() >= 5 or d.normalize() in holidays
    ]

    for i, (_, row) in enumerate(sample.iterrows()):
        pay_date = rng.choice(weekend_holiday_pool).date()
        tag      = f"wknd_{i}"

        injected.append({
            "_orig_idx":    None,
            "_tag":         tag,
            "vendor_id":    row["vendor_id"],
            "vendor_name":  row["vendor_name"],
            "invoice_date": row["invoice_date"],
            "payment_date": pay_date,
            "amount_inr":   row["amount_inr"],
            "approver_id":  row["approver_id"],
            "department":   row["department"],
            "payment_mode": row["payment_mode"],
            "_anomaly_type": "weekend_holiday",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "weekend_holiday",
            "group_id":              "",
            "injected_extra_amount": 0.0,
        })

    return injected, truth


def inject_round_figures(normal_df: pd.DataFrame,
                         vendors: pd.DataFrame,
                         rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 4 — Round-figure invoices from NON-retainer vendors.
    Only non-retainer vendors are flagged; retainer vendors issue rounds normally.
    Labels: one row per injected invoice, group_id blank, extra_amount = 0.
    """
    n            = int(N_NORMAL * ANOMALY_RATE)
    retainer_ids = set(vendors.loc[vendors["is_retainer"], "vendor_id"])
    pool         = normal_df[~normal_df["vendor_id"].isin(retainer_ids)]
    sample       = pool.sample(n=n, random_state=SEED + 2)
    injected, truth = [], []

    for i, (_, row) in enumerate(sample.iterrows()):
        mult   = int(rng.integers(1, 10))      # 1×10k to 9×10k = Rs 10k–90k
        amount = float(mult * 10_000)
        tag    = f"rnd_{i}"

        injected.append({
            "_orig_idx":    None,
            "_tag":         tag,
            "vendor_id":    row["vendor_id"],
            "vendor_name":  row["vendor_name"],
            "invoice_date": row["invoice_date"],
            "payment_date": row["payment_date"],
            "amount_inr":   amount,
            "approver_id":  row["approver_id"],
            "department":   row["department"],
            "payment_mode": row["payment_mode"],
            "_anomaly_type": "round_figure",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "round_figure",
            "group_id":              "",
            "injected_extra_amount": 0.0,
        })

    return injected, truth


def inject_outliers(normal_df: pd.DataFrame,
                    rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 5 — Outlier amounts (4x–8x the vendor's typical amount).
    Vendor typical = median of their normal invoices.
    injected_extra_amount = anomaly_amount − vendor_median.
    Labels: one row per injected invoice, group_id blank.
    """
    n              = int(N_NORMAL * ANOMALY_RATE)
    sample         = normal_df.sample(n=n, random_state=SEED + 3)
    vendor_medians = normal_df.groupby("vendor_id")["amount_inr"].median()
    injected, truth = [], []

    for i, (_, row) in enumerate(sample.iterrows()):
        vendor_median = float(vendor_medians.get(row["vendor_id"], row["amount_inr"]))
        multiplier    = rng.uniform(4.0, 8.0)
        amount        = round(vendor_median * multiplier, 2)
        tag           = f"out_{i}"

        injected.append({
            "_orig_idx":    None,
            "_tag":         tag,
            "vendor_id":    row["vendor_id"],
            "vendor_name":  row["vendor_name"],
            "invoice_date": row["invoice_date"],
            "payment_date": row["payment_date"],
            "amount_inr":   amount,
            "approver_id":  row["approver_id"],
            "department":   row["department"],
            "payment_mode": row["payment_mode"],
            "_anomaly_type": "outlier",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "outlier",
            "group_id":              "",
            "injected_extra_amount": round(amount - vendor_median, 2),
        })

    return injected, truth


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    rng      = np.random.default_rng(SEED)
    holidays = load_holidays()

    print("Building vendor catalogue...")
    vendors = build_vendor_catalogue(rng)

    print(f"Generating {N_NORMAL:,} normal invoices...")
    normal_df = generate_normal(vendors, rng)

    # ── Inject anomalies ──────────────────────────────────────────────────────
    print("Injecting anomalies...")
    dup_rows,   dup_truth   = inject_duplicates(normal_df, rng)
    split_rows, split_truth = inject_split_invoices(normal_df, vendors, rng)
    wknd_rows,  wknd_truth  = inject_weekend_payments(normal_df, holidays, rng)
    rnd_rows,   rnd_truth   = inject_round_figures(normal_df, vendors, rng)
    out_rows,   out_truth   = inject_outliers(normal_df, rng)

    # ── Combine and shuffle all invoice rows ──────────────────────────────────
    # Normal rows get a blank _tag so we can filter them out later
    normal_records = normal_df.to_dict("records")
    for r in normal_records:
        r["_tag"] = None

    all_rows    = normal_records + dup_rows + split_rows + wknd_rows + rnd_rows + out_rows
    invoices_df = pd.DataFrame(all_rows)
    invoices_df = invoices_df.sample(frac=1, random_state=SEED).reset_index(drop=True)

    # Assign sequential invoice_ids after shuffle
    invoices_df.insert(0, "invoice_id",
                       [f"INV{i:06d}" for i in range(1, len(invoices_df) + 1)])

    # ── Build ground_truth: one row per anomalous invoice ────────────────────
    # Build a _tag → invoice_id map from the shuffled frame
    tag_to_inv_id = (
        invoices_df[invoices_df["_tag"].notna()]
        .set_index("_tag")["invoice_id"]
        .to_dict()
    )

    # Combine all truth lists; assign invoice_ids via tag
    all_truth = dup_truth + split_truth + wknd_truth + rnd_truth + out_truth
    for t in all_truth:
        t["invoice_id"] = tag_to_inv_id[t["_tag"]]

    ground_truth_df = pd.DataFrame(all_truth)[
        ["invoice_id", "anomaly_type", "group_id", "injected_extra_amount"]
    ]

    # ── Strip internal columns before saving ─────────────────────────────────
    invoices_df = invoices_df.drop(
        columns=["_orig_idx", "_tag", "_anomaly_type"]
    )

    # ── Save ──────────────────────────────────────────────────────────────────
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    invoices_df.to_csv(INVOICES_PATH, index=False)
    ground_truth_df.to_csv(GROUND_TRUTH_PATH, index=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    gt_ids   = set(ground_truth_df["invoice_id"])
    normal   = invoices_df[~invoices_df["invoice_id"].isin(gt_ids)]
    inv_dates = pd.to_datetime(invoices_df["invoice_date"])

    # Weekend % among normal invoices
    normal_pay = pd.to_datetime(normal["payment_date"])
    wknd_normal_cnt = (normal_pay.dt.weekday >= 5).sum()
    wknd_pct        = wknd_normal_cnt / len(normal) * 100

    print("\n" + "=" * 65)
    print("STAGE A — DATA GENERATION SUMMARY")
    print("=" * 65)
    print(f"Total invoices written      : {len(invoices_df):,}")
    print(f"Normal invoices             : {len(normal):,}")
    print(f"Anomalous invoices          : {len(gt_ids):,}")
    print(f"Ground-truth rows           : {len(ground_truth_df):,}  "
          f"(should equal anomalous invoices)")
    print(f"Row count matches?          : "
          f"{'YES' if len(gt_ids) == len(ground_truth_df) else 'NO ← MISMATCH'}")
    print()
    print(f"{'Anomaly type':<22} {'GT rows':>8} {'Invoices in CSV':>16} "
          f"{'Extra amount (Rs)':>20}")
    print("-" * 70)
    for atype in ["duplicate", "split_invoice", "weekend_holiday",
                  "round_figure", "outlier"]:
        gt_cnt  = (ground_truth_df["anomaly_type"] == atype).sum()
        inv_cnt = (invoices_df["invoice_id"].isin(
            ground_truth_df.loc[ground_truth_df["anomaly_type"] == atype, "invoice_id"]
        )).sum()
        extra = ground_truth_df.loc[
            ground_truth_df["anomaly_type"] == atype, "injected_extra_amount"
        ].sum()
        print(f"  {atype:<20} {gt_cnt:>8} {inv_cnt:>16} {extra:>20,.2f}")

    print()
    print(f"Invoice date range          : "
          f"{inv_dates.min().date()} to {inv_dates.max().date()}")
    print(f"Total invoice amount        : Rs {invoices_df['amount_inr'].sum():>20,.2f}")
    print(f"Anomaly % of total rows     : "
          f"{len(gt_ids)/len(invoices_df)*100:.2f}%")
    print(f"Weekend payments (normal)   : "
          f"{wknd_normal_cnt} ({wknd_pct:.2f}%)  target ~2%")
    print()
    print("Files written:")
    print(f"  {INVOICES_PATH}")
    print(f"  {GROUND_TRUTH_PATH}")
    print("=" * 65)


if __name__ == "__main__":
    main()
