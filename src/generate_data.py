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

Design notes (v4)
-----------------
  - seed=42 everywhere -> fully reproducible; two runs produce identical files
  - Retainer vendors use dynamic-granularity rounding (~1% of amount,
    min Rs 100) to give realistic stable-ish amounts without flooding
    the duplicate rule with identical-amount invoices.
  - Normal payments: snap to weekday first, then 2% coin-flip to weekend.
    Normal payments avoid holiday dates except for a rare ~0.04%.
  - duplicate injection: new invoice has invoice_date = original + 1-7 days
    (not same-date), so the SQL join sees a strictly positive gap and does
    not symmetrically flag the original.  payment_date is recomputed from
    the new invoice_date.
  - weekend_holiday injection: NO new invoices are created.  Instead 160
    existing normal invoices have only their payment_date changed to a
    Saturday or Sunday.  Total invoice count = 20,000 normal rows plus
    only the duplicates, splits, round_figures and outliers.
  - outlier injection: 4x-8x vendor MEAN (not median).
  - round_figure injection: 2x-9x -> Rs 20k-90k (consistent with SQL floor).
  - ground_truth has ONE ROW PER ANOMALOUS INVOICE with group_id column.
    For weekend_holiday, the invoice_id is the EXISTING normal invoice that
    was mutated (it stays in invoices.csv with its changed payment_date).
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

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT              = Path(__file__).parent.parent
RAW_DIR           = ROOT / "data" / "raw"
DATA_DIR          = ROOT / "data"
INVOICES_PATH     = RAW_DIR / "invoices.csv"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.csv"
HOLIDAYS_PATH     = DATA_DIR / "holidays.csv"

# ── Constants ─────────────────────────────────────────────────────────────────
N_NORMAL        = 20_000
N_VENDORS       = 150
APPROVAL_LIMIT  = 50_000
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
    Create 150 vendors with lognormal amount distributions.
    ~10% are 'retainer' vendors (rent, maintenance, software licences).
    """
    vendor_ids   = [f"VND{i:04d}" for i in range(1, N_VENDORS + 1)]
    vendor_names = [f"Vendor_{i:04d}_Ltd" for i in range(1, N_VENDORS + 1)]
    log_means    = rng.uniform(np.log(5_000), np.log(5_00_000), N_VENDORS)
    log_stds     = rng.uniform(0.3, 0.8, N_VENDORS)
    is_retainer  = rng.random(N_VENDORS) < 0.10
    home_dept    = rng.choice(DEPARTMENTS, N_VENDORS)

    return pd.DataFrame({
        "vendor_id":   vendor_ids,
        "vendor_name": vendor_names,
        "log_mean":    log_means,
        "log_std":     log_stds,
        "is_retainer": is_retainer,
        "home_dept":   home_dept,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────
def to_weekday(date: pd.Timestamp) -> pd.Timestamp:
    """Shift forward until Mon-Fri."""
    while date.weekday() >= 5:
        date += timedelta(days=1)
    return date


def to_weekend(date: pd.Timestamp, rng: np.random.Generator) -> pd.Timestamp:
    """Move to the next Saturday or Sunday (coin-flip which)."""
    days_to_sat = (5 - date.weekday()) % 7
    if days_to_sat == 0:
        days_to_sat = 7
    days_to_sun = days_to_sat + 1
    offset = int(rng.choice([days_to_sat, days_to_sun]))
    return date + timedelta(days=offset)


def payment_date_normal(invoice_date: pd.Timestamp,
                        rng: np.random.Generator,
                        holidays: set) -> pd.Timestamp:
    """
    invoice_date + 15-45 days, snapped to weekday.
    2% coin-flip -> move to weekend.
    If the result lands on a holiday, nudge to next weekday (99.7% of the time).
    """
    delta  = int(rng.integers(15, 46))
    pay_dt = invoice_date + timedelta(days=delta)
    pay_dt = to_weekday(pay_dt)

    if rng.random() < 0.02:
        pay_dt = to_weekend(pay_dt, rng)
    elif pay_dt.strftime("%Y-%m-%d") in holidays:
        if rng.random() >= 0.003:
            pay_dt = to_weekday(pay_dt + timedelta(days=1))

    return pay_dt


def sample_amount(vendor_row: pd.Series, rng: np.random.Generator) -> float:
    """
    Draw from the vendor's lognormal distribution.
    Retainer vendors: round to dynamic granularity (~1% of value, min Rs 100)
    to give stable-ish amounts without causing mass same-vendor same-amount
    collisions.
    Non-retainer: round to 2 decimal places.
    """
    raw = float(np.exp(rng.normal(vendor_row["log_mean"], vendor_row["log_std"])))
    raw = max(raw, 500.0)

    if vendor_row["is_retainer"]:
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
    Generate N_NORMAL clean invoices.
    Temp columns: _orig_idx (position index), _tag (anomaly tag, None here).
    Both are stripped before saving.
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
            "_tag":          None,          # normal: no anomaly tag
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
                      rng: np.random.Generator,
                      holidays: set) -> tuple[list[dict], list[dict]]:
    """
    Rule 1: Duplicate payment.

    Creates a NEW invoice (different invoice_id) for ~160 normal invoices.
    Key design decisions:
      - invoice_date of the duplicate = original invoice_date + 1-7 days.
        This ensures the SQL join sees a strictly positive gap (>0 days),
        eliminating the symmetry problem where same-date pairs caused both
        the original and the duplicate to be flagged.
      - payment_date is recomputed from the new invoice_date using the
        same payment_date_normal() logic, so the lag is realistic.
      - Only the NEW invoice is labelled in ground_truth; the original is not.
      - group_id = str(_orig_idx) of the source, linking duplicate to original.
      - injected_extra_amount = full amount (confirmed double payment).
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    sample = normal_df.sample(n=n, random_state=SEED)
    injected, truth = [], []

    for _, row in sample.iterrows():
        # New invoice_date is 1-7 days AFTER the original invoice_date
        orig_inv_date = pd.Timestamp(row["invoice_date"])
        date_delta    = int(rng.integers(1, 8))          # 1 to 7 days strictly > 0
        new_inv_date  = orig_inv_date + timedelta(days=date_delta)

        # Recompute a realistic payment_date from the new invoice_date
        new_pay_date = payment_date_normal(new_inv_date, rng, holidays).date()
        tag = f"dup_{row['_orig_idx']}"

        injected.append({
            "_orig_idx":     None,
            "_tag":          tag,
            "vendor_id":     row["vendor_id"],
            "vendor_name":   row["vendor_name"],
            "invoice_date":  new_inv_date.date(),   # DIFFERENT from original
            "payment_date":  new_pay_date,
            "amount_inr":    row["amount_inr"],     # SAME amount = the signal
            "approver_id":   row["approver_id"],
            "department":    row["department"],
            "payment_mode":  row["payment_mode"],
            "_anomaly_type": "duplicate",
        })
        truth.append({
            "_tag":                  tag,
            "anomaly_type":          "duplicate",
            "group_id":              str(row["_orig_idx"]),  # links to original
            "injected_extra_amount": row["amount_inr"],
        })

    return injected, truth


def inject_weekend_payments(normal_df: pd.DataFrame,
                            rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict]]:
    """
    Rule 3: Weekend payment.

    Does NOT create new invoices.  Instead mutates 160 existing normal invoices
    in-place: only their payment_date is changed to a Saturday or Sunday.
    The invoice_date, amount, vendor, and all other fields remain unchanged.

    Returns:
      mutated_df  -- a copy of normal_df with payment_dates changed for 160 rows
      truth       -- ground_truth rows (one per mutated invoice)

    The caller replaces normal_df with mutated_df before building the full
    invoice pool, so total invoice count does NOT increase for this anomaly.

    group_id blank, injected_extra_amount = 0 (exposure, not confirmed loss).
    """
    n      = int(N_NORMAL * ANOMALY_RATE)
    # Sample the row INDICES (positional) to mutate
    sampled = normal_df.sample(n=n, random_state=SEED + 1)

    # Pool of weekend dates in range
    all_dates    = pd.date_range(START_DATE, END_DATE, freq="D")
    weekend_pool = [d for d in all_dates if d.weekday() >= 5]

    # Work on a copy so normal_df is not modified in place
    mutated_df = normal_df.copy()
    truth      = []

    for i, (idx, row) in enumerate(sampled.iterrows()):
        new_pay = rng.choice(weekend_pool).date()
        mutated_df.at[idx, "payment_date"]    = new_pay
        mutated_df.at[idx, "_anomaly_type"]   = "weekend_holiday"
        mutated_df.at[idx, "_tag"]            = f"wknd_{i}"

        truth.append({
            "_tag":                  f"wknd_{i}",
            "anomaly_type":          "weekend_holiday",
            "group_id":              "",
            "injected_extra_amount": 0.0,
        })

    return mutated_df, truth


def inject_split_invoices(normal_df: pd.DataFrame,
                          vendors: pd.DataFrame,
                          rng: np.random.Generator,
                          holidays: set) -> tuple[list[dict], list[dict]]:
    """
    Rule 2: Split invoice clusters.
    2-4 new invoices, same vendor + approver, within 3 days, each 80-98% of limit.
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


def inject_round_figures(normal_df: pd.DataFrame,
                         vendors: pd.DataFrame,
                         rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    """
    Rule 4: Round-figure invoices from non-retainer vendors.
    Multiplier 2-9 -> Rs 20k-90k (consistent with SQL floor of Rs 20,000).
    group_id blank, extra_amount = 0.
    """
    n            = int(N_NORMAL * ANOMALY_RATE)
    retainer_ids = set(vendors.loc[vendors["is_retainer"], "vendor_id"])
    pool         = normal_df[~normal_df["vendor_id"].isin(retainer_ids)]
    sample       = pool.sample(n=n, random_state=SEED + 2)
    injected, truth = [], []

    for i, (_, row) in enumerate(sample.iterrows()):
        mult   = int(rng.integers(2, 10))
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
    4x-8x vendor MEAN (not median) -> injected amounts reliably exceed
    the mean+3*std threshold used in the SQL rule.
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

    # weekend_holiday: mutates normal_df in-place (no new rows)
    # Must happen BEFORE normal_df is used as the base pool, so the
    # mutated payment_dates are present in the final invoice rows.
    normal_df, wknd_truth = inject_weekend_payments(normal_df, rng)

    # All other anomaly types create new invoice rows
    dup_rows,   dup_truth   = inject_duplicates(normal_df, rng, holidays)
    split_rows, split_truth = inject_split_invoices(normal_df, vendors, rng, holidays)
    rnd_rows,   rnd_truth   = inject_round_figures(normal_df, vendors, rng)
    out_rows,   out_truth   = inject_outliers(normal_df, rng)

    # Combine: normal rows (already include mutated weekend rows) + new rows
    normal_records = normal_df.to_dict("records")
    all_rows    = normal_records + dup_rows + split_rows + rnd_rows + out_rows
    invoices_df = pd.DataFrame(all_rows)
    invoices_df = invoices_df.sample(frac=1, random_state=SEED).reset_index(drop=True)
    invoices_df.insert(0, "invoice_id",
                       [f"INV{i:06d}" for i in range(1, len(invoices_df) + 1)])

    # Build ground truth via _tag -> invoice_id mapping
    # All anomaly types (including weekend, which has _tag set on normal rows)
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

    # Strip temp columns before saving
    invoices_df = invoices_df.drop(columns=["_orig_idx", "_tag", "_anomaly_type"])

    # Save
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    invoices_df.to_csv(INVOICES_PATH, index=False)
    ground_truth_df.to_csv(GROUND_TRUTH_PATH, index=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    gt_ids     = set(ground_truth_df["invoice_id"])
    wknd_ids   = set(ground_truth_df.loc[
        ground_truth_df["anomaly_type"] == "weekend_holiday", "invoice_id"
    ])
    # 'normal' here = invoices not in ground_truth at all
    # (weekend invoices ARE in gt but are part of the 20k base rows)
    non_gt_ids = set(invoices_df["invoice_id"]) - gt_ids
    inv_dates  = pd.to_datetime(invoices_df["invoice_date"])

    # Weekend % among all 20k base invoices (including the mutated ones)
    base_inv    = invoices_df[invoices_df["invoice_id"].isin(
        set(invoices_df["invoice_id"]) - (gt_ids - wknd_ids)
    )]
    # Simpler: just count weekend payment_dates across the full 20k base
    # The base = all invoices except the newly-created anomaly types
    new_anomaly_ids = gt_ids - wknd_ids   # dup + split + round + outlier
    base_20k = invoices_df[~invoices_df["invoice_id"].isin(new_anomaly_ids)]
    base_pay  = pd.to_datetime(base_20k["payment_date"])
    wknd_cnt  = (base_pay.dt.weekday >= 5).sum()
    wknd_pct  = wknd_cnt / len(base_20k) * 100

    hdf          = pd.read_csv(HOLIDAYS_PATH, parse_dates=["date"])
    holiday_strs = set(hdf["date"].dt.strftime("%Y-%m-%d"))
    # Holiday count among base invoices that are NOT the injected weekend ones
    pure_normal  = base_20k[~base_20k["invoice_id"].isin(wknd_ids)]
    hol_cnt      = pure_normal["payment_date"].astype(str).isin(holiday_strs).sum()
    hol_pct      = hol_cnt / len(pure_normal) * 100

    print("\n" + "=" * 70)
    print("STAGE A DATA GENERATION SUMMARY")
    print("=" * 70)
    print(f"Total invoices written          : {len(invoices_df):,}")
    print(f"  Base (20k normal, incl mutated): {len(base_20k):,}")
    print(f"  New anomaly rows (dup+split+")
    print(f"  round+outlier)                : {len(invoices_df) - len(base_20k):,}")
    print(f"Ground-truth rows               : {len(ground_truth_df):,}")
    print(f"  of which mutated-in-place     : {len(wknd_ids):,}  (weekend_holiday)")
    print(f"  of which new invoice rows     : {len(gt_ids) - len(wknd_ids):,}")
    print(f"Row count matches?              : "
          f"{'YES' if len(gt_ids) == len(ground_truth_df) else 'NO MISMATCH'}")
    print()
    print(f"{'Anomaly type':<22} {'GT rows':>8} {'New rows':>10} "
          f"{'Extra amount (Rs)':>20}")
    print("-" * 65)
    for atype, new_rows_list in [
        ("duplicate",      dup_rows),
        ("split_invoice",  split_rows),
        ("weekend_holiday", []),        # no new rows
        ("round_figure",   rnd_rows),
        ("outlier",        out_rows),
    ]:
        gt_cnt  = (ground_truth_df["anomaly_type"] == atype).sum()
        new_cnt = len(new_rows_list)
        extra   = ground_truth_df.loc[
            ground_truth_df["anomaly_type"] == atype, "injected_extra_amount"
        ].sum()
        note = " (mutated)" if atype == "weekend_holiday" else ""
        print(f"  {atype:<20} {gt_cnt:>8} {new_cnt:>10}{note:12} {extra:>20,.2f}")

    print()
    print(f"Invoice date range              : "
          f"{inv_dates.min().date()} to {inv_dates.max().date()}")
    print(f"Total invoice amount            : "
          f"Rs {invoices_df['amount_inr'].sum():>20,.2f}")
    print(f"Weekend pay among base 20k      : {wknd_cnt} ({wknd_pct:.2f}%)  "
          f"(incl 160 injected; pure-normal ~2%)")
    print(f"Holiday pay among pure-normal   : {hol_cnt} ({hol_pct:.2f}%)")
    print()
    print("Files written:")
    print(f"  {INVOICES_PATH}")
    print(f"  {GROUND_TRUTH_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
