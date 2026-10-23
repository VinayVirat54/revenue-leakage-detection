# DATA_DICTIONARY.md

Describes every column in every file produced by Stage A.

---

## data/raw/invoices.csv

The main dataset. Each row is one vendor invoice.

| Column | Type | Description |
|---|---|---|
| `invoice_id` | string | Unique identifier, format `INV000001`. Primary key. |
| `vendor_id` | string | Vendor identifier, format `VND0001`. Foreign key to vendor catalogue. |
| `vendor_name` | string | Human-readable vendor name, e.g. `Vendor_0001_Ltd`. |
| `invoice_date` | date (YYYY-MM-DD) | Date the vendor raised the invoice. |
| `payment_date` | date (YYYY-MM-DD) | Date the company actually paid the invoice. |
| `amount_inr` | float | Invoice amount in Indian Rupees (INR). Always ≥ Rs 500. |
| `approver_id` | string | ID of the employee who approved this payment, format `APR001`. |
| `department` | string | Internal department that requested / owns the invoice. One of: Finance, IT, Operations, HR, Marketing, Procurement, Legal, Facilities, R&D, Admin. |
| `payment_mode` | string | How the payment was made. One of: `NEFT`, `RTGS`, `UPI`, `Cheque`. |

**Notes**
- Dates span 3 Indian financial years: 2022-04-01 to 2025-03-31.
- ~96% of rows are realistic normal invoices; ~4% are injected anomalies (the marking column is removed — use `ground_truth.csv` for labels).
- About 2% of normal invoices have a payment on a weekend (deliberate noise so weekend rule is not trivially perfect).
- About 5% of retainer-type vendors naturally issue round-figure amounts (deliberate noise for round-figure rule).

---

## data/ground_truth.csv

Labels for the injected anomalies. Used in Stage C to measure precision, recall, and F1.

| Column | Type | Description |
|---|---|---|
| `invoice_id` | string | Matches `invoice_id` in `invoices.csv`. One row per anomalous invoice. |
| `anomaly_type` | string | Type of injected anomaly. One of the five values below. |
| `group_id` | string | Links related anomalous invoices together. For `duplicate`: the `_orig_idx` of the original invoice (links the duplicate back to its source). For `split_invoice`: a cluster identifier like `split_cluster_0042` (all invoices in the same cluster share this id). Blank for `weekend_holiday`, `round_figure`, and `outlier`. |
| `injected_extra_amount` | float | INR amount representing the financial exposure for this anomaly (see per-type notes). |

**anomaly_type values**

| Value | What was injected | injected_extra_amount meaning |
|---|---|---|
| `duplicate` | Exact copy of a real invoice — same vendor, same amount, paid 1–7 days later. Only the new (later) invoice is labelled. | Full amount of the duplicate invoice (= confirmed double payment) |
| `split_invoice` | Cluster of 2–4 invoices from the same vendor + approver within 3 days, each 80–98% of Rs 50,000. Every invoice in the cluster gets its own row; all share the same `group_id`. | Individual invoice amount (auditor uses `group_id` to sum the full cluster) |
| `weekend_holiday` | Payment date forced to a Saturday, Sunday, or Indian national holiday | 0 — exposure risk, not a confirmed loss |
| `round_figure` | Amount forced to an exact multiple of Rs 10,000, for a non-retainer vendor | 0 — exposure risk, not a confirmed loss |
| `outlier` | Amount set to 4x–8x the vendor's typical (median) amount | amount − vendor_median (potential overbilling above normal) |

---

## data/holidays.csv

Reference table of Indian national holidays used by Rule 3.

| Column | Type | Description |
|---|---|---|
| `date` | date (YYYY-MM-DD) | Calendar date of the holiday. |
| `holiday_name` | string | Name of the holiday (e.g. `Republic Day`). |

**Notes**
- Covers FY 2022-23, 2023-24, 2024-25 only.
- Does not include state-specific or bank-specific holidays (known limitation).

---

## outputs/scored_invoices.csv *(written in Stage C)*

| Column | Type | Description |
|---|---|---|
| `invoice_id` | string | From invoices.csv |
| `flag_duplicate` | int (0/1) | 1 if Rule 1 flagged this invoice |
| `flag_split` | int (0/1) | 1 if Rule 2 flagged this invoice |
| `flag_weekend` | int (0/1) | 1 if Rule 3 flagged this invoice |
| `flag_round` | int (0/1) | 1 if Rule 4 flagged this invoice |
| `flag_outlier` | int (0/1) | 1 if Rule 5 flagged this invoice |
| `flag_count` | int | Total number of rules that flagged this invoice |
| `risk_score` | float (0–1) | Weighted risk score (from config.yaml weights) |
| `risk_tier` | string | `HIGH` / `MEDIUM` / `LOW` (from config.yaml thresholds) |
| `confirmed_leakage_inr` | float | Rs amount considered a near-certain loss (duplicates + outlier excess) |
| `exposure_at_risk_inr` | float | Rs amount suspicious but not confirmed lost |

---

## outputs/vendor_risk.csv *(written in Stage C)*

| Column | Type | Description |
|---|---|---|
| `vendor_id` | string | Vendor identifier |
| `vendor_name` | string | Vendor name |
| `total_invoices` | int | Count of all invoices for this vendor |
| `flagged_invoices` | int | Count of invoices flagged by at least one rule |
| `flag_rate_pct` | float | flagged_invoices / total_invoices × 100 |
| `total_spend_inr` | float | Sum of all invoice amounts |
| `confirmed_leakage_inr` | float | Sum of confirmed leakage amounts |
| `exposure_at_risk_inr` | float | Sum of exposure-at-risk amounts |
| `max_risk_score` | float | Highest single-invoice risk score for this vendor |
| `avg_risk_score` | float | Mean risk score across all invoices |
| `risk_tier` | string | Vendor-level tier based on max_risk_score |
