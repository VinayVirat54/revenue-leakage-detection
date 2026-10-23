"""
generate_data.py
================
Generates ~20,000 synthetic vendor invoices for 150 vendors over 3 Indian
financial years (2022-04-01 to 2025-03-31).

Outputs
-------
  data/raw/invoices.csv       -- all invoices (normal + anomalous)
  data/ground_truth.csv       -- one row per anomalous invoice
  data/holidays.csv           -- must already exist (hardcoded Indian holidays)

Usage
-----
  python src/generate_data.py          (run from project root)

Design notes (v3 fixes applied)
--------------------------------
  - seed=42 everywhere -> fully reproducible; two runs produce identical files
  - Retainer vendors use a FIXED FEE (chosen once per vendor from a sensible
    range) rather than rounding every lognormal draw to 10,000.  This prevents
    hundreds of identical-amount invoices that flood Rule 1 with false positives.
    About 0.3% of retainer invoices deviate from the fixed fee by +/- 5-10%
    (e.g. occasional surcharges), keeping a small natural repeat count.
  - Normal payments: snap to weekday first, then 2% coin-flip to weekend.
    Normal payments also avoid holiday dates except for ~0.3% of invoices
    (rare urgent/standing-order payments on holidays).
  - outlier injection: multiplier of 4x-8x the vendor's MEAN (not median),
    so injected amounts are genuinely far above the mean+3*std threshold.
  - round_figure injection: multiplier 2x to 9x (i.e. Rs 20k to 90k),
    consistent with the SQL rule's Rs 20,000 floor.
  - weekend_holiday injection: picks only Saturday/Sunday, not holidays,
    so injected anomalies are clearly suspicious and not confused with the
    small ~0.3% natural holiday payments.
  - ground_truth has ONE ROW PER ANOMALOUS INVOICE with group_id column.
"""

import random
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
N_NORMAL        = 20_000
N_VENDORS       = 150
APPROVAL_LIMIT  = 50_000          # INR
START_DATE      = pd.Timestamp("2022-04-01")
END_DATE        = pd.Timestamp("2025-03-31")
ANOMALY_RATE    = 0.008           # ~160 per anomaly type

DEPARTMENTS = [
    "Finance", "IT", "Operations", "HR", "Marketing",
    "Procurement", "Legal", "Facilities", "R&D", "Admin",
]
PAYMENT_MODES        = ["NEFT", "RTGS", "UPI", "Cheque"]
PAYMENT_MODE_WEIGHTS = [0.45, 0.25, 0.20, 0.10]


# ── Load holiday set ──────────────────────────────────────────────────────────
def load_holidays() -> set:
    """Return a set of holiday dates as strings 'YYYY-MM-DD'."""
    hdf = pd.read_csv(HOLIDAYS_PATH, parse_dates=["date"])
    return set(hdf["date"].dt.strftime("%Y-%m-%d"))


# ── Vendor catalogue ──────────────────────────────────────────────────────────
def build_vendor_catalogue(rng: np.random.Generator) -> pd.DataFrame:
    """
    Create 150 vendors.  Each vendor gets:
      - log_mean / log_std : lognormal params for invoice amounts
      - is_retainer        : True for ~10% of vendors (rent, maintenance,
                             software licences -- amounts are stable but not
                             identical every month; rounded to nearest 500
                             rather than 10,000 to avoid mass collisions)
      - home_dept          : the department that mostly uses this vendor
    """
    vendor_ids   = [f"VND{i:04d}" for i in range(1, N_VENDORS + 1)]
    vendor_names = [f"Vendor_{i:04d}_Ltd" for i in range(1, N_VENDORS + 1)]

    log_means   = rng.uniform(np.log(5_000), np.log(5_00_000), N_VENDORS)
    log_stds    = rng.uniform(0.3, 0.8, N_VENDORS)
    is_retainer = rng.random(N_VENDORS) < 0.10    # ~15 retainer vendors
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
    """Shift forward until Mon-Fri."""
    while date.weekday() >= 5:
        date += timedelta(days=1)
    return date


# ── Helper: move to nearest upcoming Saturday or Sunday ──────────────────────
def to_weekend(date: pd.Timestamp, rng: np.random.Generator) -> pd.Timestamp:
    """Move to the next Saturday or Sunday (coin-flip which)."""
    days_to_sat = (5 - date.weekday()) % 7
    if days_to_sat == 0:
        days_to_sat = 7             # already Saturday, go to next one
    days_to_sun = days_to_sat + 1
    offset = int(rng.choice([days_to_sat, days_to_sun]))
    return date + timedelta(days=offset)


# ── Helper: payment date for normal invoices ──────────────────────────────────
def payment_date_normal(invoice_date: pd.Timestamp,
                        rng: np.random.Generator,
                        holidays: set) -> pd.Timestamp:
    """
    invoice_date + 15-45 days.
    Step 1: snap to weekday.
    Step 2: 2% coin-flip -> move to weekend (Sat/Sun).
    Step 3: if still on a holiday, nudge to next weekday UNLESS a 0.3%
            coin-flip says 'leave it' (rare urgent/standing-order payment).
    This gives ~2% weekend noise and ~0.3% holiday noise in normal data.
    """
    delta  = int(rng.integers(15, 46))
    pay_dt = invoice_date + timedelta(days=delta)
    pay_dt = to_weekday(pay_dt)                    # step 1

    if rng.random() < 0.02:                        # step 2: 2% -> weekend
        pay_dt = to_weekend(pay_dt, rng)
    elif pay_dt.strftime("%Y-%m-%d") in holidays:  # step 3: on a holiday
        if rng.random() >= 0.003:                  # 99.7% -> nudge off holiday
            pay_dt = to_weekday(pay_dt + timedelta(days=1))

    return pay_dt


# ── Helper: sample invoice amount ────────────────────────────────────────────
def sample_amount(vendor_row: pd.Series, rng: np.random.Generator) -> float:
    """
    Draw an invoice amount from the vendor's lognormal distribution.
    - Non-retainer vendors: round to 2 decimal places (paise precision).
      The lognormal spread means amounts vary widely and exact repeats
      are statistically near-zero.
    - Retainer vendors: round to the nearest 'granularity' where granularity
      is scaled to 1% of the draw (minimum Rs 100).
      Example: Rs 8,432 -> granularity 84 -> rounds to Rs 8,400.
               Rs 92,500 -> granularity 925 -> rounds to Rs 92,500 (925 step).
      This gives stable-ish 'retainer' amounts (rounded, not jagged) while
      avoiding mass collisions at Rs 10,000 or Rs 6,000 etc.
    """
    raw = float(np.exp(rng.normal(vendor_row["log_mean"], vendor_row["log_std"])))
    raw = max(raw, 500.0)

    if vendor_row["is_retainer"]:
        # Dynamic granularity: ~1% of value, minimum Rs 100
        gran = max(100.0, round(raw * 0.01 / 100) * 100)
        raw  = round(raw / gran) * gran
        raw  = max(raw, 500.0)
        return float(raw)
    else:
        return round(raw, 2)


# ── Generate normal invoices ──────────────────────────────────────────────────
def generate_normal(vendors: pd.DataFrame,
                    rng: np.random.Generator,
                    holidays: set) -> pd.DataFrame:
    """
    Generate N_NORMAL clean (no-anomaly) invoices.
    _orig_idx and _tag are temp columns stripped before saving.
    """
    total_days   = (END_DATE - START_DATE).days
    approver_ids = [f"APR{i:03d}" for i in range(1, 21)]
    records      = []

    for i in range(N_NORMAL):
        v_idx  = int(rng.integers(0, N_VENDORS))
        vendor = vendors.iloc[v_idx]

        inv_date = START_DATE + timedelta(days=int(rng.integers(0, total_days)))
        pay_date = payment_date_normal(inv_date, rng, holidays)
        amount   = sample_amount(vendor, rng)
        dept     = vendor["home_dept"] if rng.random() < 0.80 else rng.choice(DEPARTMENTS)

        records.append({
            "_orig_idx":     i,
            "_tag":          None,
            "vendor_id":     vendor["vendor_id"],
            "vendor_name":   vendor["vendor_name"],
            "invoice_date":  inv_date.date(),
            "payment_date":  pay_date.date(),
            "amount_inr":    amount,
            "approver_id":   rng.choice(approver_ids),
            "department":    dept,
            "payment_mode":  rng.choice(PAYMENT_MODES, p=PAYMENT_MODE_WEIGHTS),
            "_anomaly_type": None,
        })

    return pd.DataFrame(records)


# ══════════════════════════════════════════════════════════════════════════════
# Anomaly injectors
# ══════════════════════════════════════════════════════════════════════════════

def inject_duplicates(normal_df: pd.DataFrame,
                      rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 1: Duplicate payment.
    Same vendor, same amount, different invoice_id, payment 1-7 days later.
    Only the NEW (later) invoice is labelled.
    group_id = _orig_idx of the source invoice.
    injected_extra_amount = full amount (confirmed double payment).
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    sample = normal_df.sample(n=n, random_state=SEED)
    injected, truth = [], []

    for _, row in sample.iterrows():
        orig_pay = pd.Timestamp(row["payment_date"])
        delta    = int(rng.integers(1, 8))
        new_pay  = (orig_pay + timedelta(days=delta)).date()
        tag      = f"dup_{row['_orig_idx']}"

        injected.append({
            "_orig_idx":     None,
            "_tag":          tag,
            "vendor_id":     row["vendor_id"],
            "vendor_name":   row["vendor_name"],
            "invoice_date":  row["invoice_date"],
            "payment_date":  new_pay,
            "amount_inr":    row["amount_inr"],
            "approver_id":   row["approver_id"],
            "department":    row["department"],
            "payment_mode":  row["payment_mode"],
            "_anomaly_type": "duplicate",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "duplicate",
            "group_id":              str(row["_orig_idx"]),
            "injected_extra_amount": row["amount_inr"],
        })

    return injected, truth


def inject_split_invoices(normal_df: pd.DataFrame,
                          vendors: pd.DataFrame,
                          rng: np.random.Generator,
                          holidays: set) -> tuple[list[dict], list[dict]]:
    """
    Rule 2: Split invoice clusters.
    2-4 invoices, same vendor + approver, within 3 days, each 80-98% of limit.
    Every invoice in the cluster gets its own ground_truth row.
    group_id shared across the cluster.
    """
    n_clusters   = int(N_NORMAL * ANOMALY_RATE)
    approver_ids = [f"APR{i:03d}" for i in range(1, 21)]
    total_days   = (END_DATE - START_DATE).days
    injected, truth = [], []

    for cluster_idx in range(n_clusters):
        v_idx        = int(rng.integers(0, N_VENDORS))
        vendor       = vendors.iloc[v_idx]
        cluster_size = int(rng.integers(2, 5))
        base_date    = START_DATE + timedelta(days=int(rng.integers(0, total_days - 5)))
        approver     = rng.choice(approver_ids)
        department   = vendor["home_dept"]
        group_id     = f"split_cluster_{cluster_idx:04d}"

        for j in range(cluster_size):
            pct      = rng.uniform(0.80, 0.98)
            amount   = round(APPROVAL_LIMIT * pct, 2)
            inv_date = (base_date + timedelta(days=int(rng.integers(0, 4)))).date()
            pay_date = payment_date_normal(pd.Timestamp(inv_date), rng, holidays).date()
            tag      = f"split_{cluster_idx}_{j}"

            injected.append({
                "_orig_idx":     None,
                "_tag":          tag,
                "vendor_id":     vendor["vendor_id"],
                "vendor_name":   vendor["vendor_name"],
                "invoice_date":  inv_date,
                "payment_date":  pay_date,
                "amount_inr":    amount,
                "approver_id":   approver,
                "department":    department,
                "payment_mode":  rng.choice(PAYMENT_MODES, p=PAYMENT_MODE_WEIGHTS),
                "_anomaly_type": "split_invoice",
            })
            truth.append({
                "_tag":                  tag,
                "anomaly_type":          "split_invoice",
                "group_id":              group_id,
                "injected_extra_amount": amount,
            })

    return injected, truth


def inject_weekend_payments(normal_df: pd.DataFrame,
                            rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 3: Weekend payment.
    Force payment_date to Saturday or Sunday only (not holidays), so injected
    anomalies are clearly on non-working days and not confused with the small
    natural holiday-payment noise.
    group_id blank, extra_amount = 0 (exposure, not confirmed loss).
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    sample = normal_df.sample(n=n, random_state=SEED + 1)
    injected, truth = [], []

    # Pool of Saturday and Sunday dates in range only
    all_dates     = pd.date_range(START_DATE, END_DATE, freq="D")
    weekend_pool  = [d for d in all_dates if d.weekday() >= 5]

    for i, (_, row) in enumerate(sample.iterrows()):
        pay_date = rng.choice(weekend_pool).date()
        tag      = f"wknd_{i}"

        injected.append({
            "_orig_idx":     None,
            "_tag":          tag,
            "vendor_id":     row["vendor_id"],
            "vendor_name":   row["vendor_name"],
            "invoice_date":  row["invoice_date"],
            "payment_date":  pay_date,
            "amount_inr":    row["amount_inr"],
            "approver_id":   row["approver_id"],
            "department":    row["department"],
            "payment_mode":  row["payment_mode"],
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
    Rule 4: Round-figure invoices from non-retainer vendors.
    Multiplier is 2 to 9 (inclusive) -> amounts Rs 20,000 to Rs 90,000.
    Minimum Rs 20,000 is consistent with the SQL rule floor.
    group_id blank, extra_amount = 0.
    """
    n            = int(N_NORMAL * ANOMALY_RATE)
    retainer_ids = set(vendors.loc[vendors["is_retainer"], "vendor_id"])
    pool         = normal_df[~normal_df["vendor_id"].isin(retainer_ids)]
    sample       = pool.sample(n=n, random_state=SEED + 2)
    injected, truth = [], []

    for i, (_, row) in enumerate(sample.iterrows()):
        mult   = int(rng.integers(2, 10))      # 2 to 9 -> Rs 20k to Rs 90k
        amount = float(mult * 10_000)
        tag    = f"rnd_{i}"

        injected.append({
            "_orig_idx":     None,
            "_tag":          tag,
            "vendor_id":     row["vendor_id"],
            "vendor_name":   row["vendor_name"],
            "invoice_date":  row["invoice_date"],
            "payment_date":  row["payment_date"],
            "amount_inr":    amount,
            "approver_id":   row["approver_id"],
            "department":    row["department"],
            "payment_mode":  row["payment_mode"],
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
    Rule 5: Outlier amounts.
    Use vendor MEAN (not median) as the baseline, multiplier 4x-8x.
    Since the SQL rule is mean + 3*std, using the mean directly ensures
    injected amounts are more likely to exceed the 3-sigma threshold.
    A 4x-mean amount is at least (4*mean - mean) / std = 3*mean/std above
    the mean; for most vendors this exceeds 3*std.
    injected_extra_amount = amount - vendor_mean.
    group_id blank.
    """
    n            = int(N_NORMAL * ANOMALY_RATE)
    sample       = normal_df.sample(n=n, random_state=SEED + 3)
    vendor_means = normal_df.groupby("vendor_id")["amount_inr"].mean()
    injected, truth = [], []

    for i, (_, row) in enumerate(sample.iterrows()):
        vendor_mean = float(vendor_means.get(row["vendor_id"], row["amount_inr"]))
        multiplier  = rng.uniform(4.0, 8.0)
        amount      = round(vendor_mean * multiplier, 2)
        tag         = f"out_{i}"

        injected.append({
            "_orig_idx":     None,
            "_tag":          tag,
            "vendor_id":     row["vendor_id"],
            "vendor_name":   row["vendor_name"],
            "invoice_date":  row["invoice_date"],
            "payment_date":  row["payment_date"],
            "amount_inr":    amount,
            "approver_id":   row["approver_id"],
            "department":    row["department"],
            "payment_mode":  row["payment_mode"],
            "_anomaly_type": "outlier",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "outlier",
            "group_id":              "",
            "injected_extra_amount": round(amount - vendor_mean, 2),
        })

    return injected, truth


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    rng      = np.random.default_rng(SEED)
    holidays = load_holidays()

    print("Building vendor catalogue...")
    vendors = build_vendor_catalogue(rng)

    print(f"Generating {N_NORMAL:,} normal invoices...")
    normal_df = generate_normal(vendors, rng, holidays)

    print("Injecting anomalies...")
    dup_rows,   dup_truth   = inject_duplicates(normal_df, rng)
    split_rows, split_truth = inject_split_invoices(normal_df, vendors, rng, holidays)
    wknd_rows,  wknd_truth  = inject_weekend_payments(normal_df, rng)
    rnd_rows,   rnd_truth   = inject_round_figures(normal_df, vendors, rng)
    out_rows,   out_truth   = inject_outliers(normal_df, rng)

    # Combine and shuffle
    normal_records = normal_df.to_dict("records")
    all_rows    = normal_records + dup_rows + split_rows + wknd_rows + rnd_rows + out_rows
    invoices_df = pd.DataFrame(all_rows)
    invoices_df = invoices_df.sample(frac=1, random_state=SEED).reset_index(drop=True)
    invoices_df.insert(0, "invoice_id",
                       [f"INV{i:06d}" for i in range(1, len(invoices_df) + 1)])

    # Build ground truth via _tag -> invoice_id mapping
    tag_to_inv_id = (
        invoices_df[invoices_df["_tag"].notna()]
        .set_index("_tag")["invoice_id"]
        .to_dict()
    )
    all_truth = dup_truth + split_truth + wknd_truth + rnd_truth + out_truth
    for t in all_truth:
        t["invoice_id"] = tag_to_inv_id[t["_tag"]]

    ground_truth_df = pd.DataFrame(all_truth)[
        ["invoice_id", "anomaly_type", "group_id", "injected_extra_amount"]
    ]

    # Strip temp columns
    invoices_df = invoices_df.drop(columns=["_orig_idx", "_tag", "_anomaly_type"])

    # Save
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    invoices_df.to_csv(INVOICES_PATH, index=False)
    ground_truth_df.to_csv(GROUND_TRUTH_PATH, index=False)

    # Summary
    gt_ids    = set(ground_truth_df["invoice_id"])
    normal    = invoices_df[~invoices_df["invoice_id"].isin(gt_ids)]
    inv_dates = pd.to_datetime(invoices_df["invoice_date"])
    normal_pay = pd.to_datetime(normal["payment_date"])

    # Load holiday set for counting
    hdf = pd.read_csv(HOLIDAYS_PATH, parse_dates=["date"])
    holiday_strs = set(hdf["date"].dt.strftime("%Y-%m-%d"))

    wknd_cnt    = (normal_pay.dt.weekday >= 5).sum()
    # payment_date column is stored as string 'YYYY-MM-DD' in the DataFrame
    # after .date() was called; convert to string for comparison
    normal_pay_str = normal["payment_date"].astype(str)
    holiday_cnt = normal_pay_str.isin(holiday_strs).sum()
    wknd_pct    = wknd_cnt / len(normal) * 100
    hol_pct     = holiday_cnt / len(normal) * 100

    print("\n" + "=" * 65)
    print("STAGE A DATA GENERATION SUMMARY")
    print("=" * 65)
    print(f"Total invoices written      : {len(invoices_df):,}")
    print(f"Normal invoices             : {len(normal):,}")
    print(f"Anomalous invoices          : {len(gt_ids):,}")
    print(f"Ground-truth rows           : {len(ground_truth_df):,}  "
          f"(should equal anomalous invoices)")
    print(f"Row count matches?          : "
          f"{'YES' if len(gt_ids) == len(ground_truth_df) else 'NO MISMATCH'}")
    print()
    print(f"{'Anomaly type':<22} {'GT rows':>8} {'Invoices in CSV':>16} "
          f"{'Extra amount (Rs)':>20}")
    print("-" * 70)
    for atype in ["duplicate", "split_invoice", "weekend_holiday",
                  "round_figure", "outlier"]:
        gt_cnt  = (ground_truth_df["anomaly_type"] == atype).sum()
        inv_cnt = (invoices_df["invoice_id"].isin(
            ground_truth_df.loc[
                ground_truth_df["anomaly_type"] == atype, "invoice_id"
            ]
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
    print(f"Weekend payments (normal)   : {wknd_cnt} ({wknd_pct:.2f}%)  target ~2%")
    print(f"Holiday payments (normal)   : {holiday_cnt} ({hol_pct:.2f}%)  target ~0.3%")
    print()
    print("Files written:")
    print(f"  {INVOICES_PATH}")
    print(f"  {GROUND_TRUTH_PATH}")
    print("=" * 65)


if __name__ == "__main__":
    main()
