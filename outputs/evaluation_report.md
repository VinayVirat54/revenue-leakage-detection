# Evaluation Report

This report evaluates the five detection rules against the injected anomalies in `data/ground_truth.csv`. Because the dataset is **synthetic** and the anomalies were designed to match the rules, recall figures are higher than would be expected on real data. See the Limitations section.

## 1. Per-Rule Detection Metrics

| Rule | Flagged | GT count | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Rule 1: Duplicates | 177 | 160 | 160 | 17 | 0 | 90.4% | 100.0% | 95.0% |
| Rule 2: Split Invoices | 489 | 471 | 471 | 18 | 0 | 96.3% | 100.0% | 98.1% |
| Rule 3: Weekend/Holiday | 591 | 160 | 160 | 431 | 0 | 27.1% | 100.0% | 42.6% |
| Rule 4: Round Figure | 200 | 160 | 160 | 40 | 0 | 80.0% | 100.0% | 88.9% |
| Rule 5: Outliers | 392 | 160 | 156 | 236 | 4 | 39.8% | 97.5% | 56.5% |

## 2. Overall Metrics (Any Rule vs Any Anomaly)

| Metric | Value |
|---|---|
| Total invoices | 20,951 |
| Flagged by any rule | 1,731 |
| Ground-truth anomalies | 1,111 |
| TP (flagged + in GT) | 1,107 |
| FP (flagged + not in GT) | 624 |
| FN (in GT + not flagged) | 4 |
| Precision | 63.9% |
| Recall | 99.6% |
| F1 | 77.9% |

## 3. Risk Level Distribution

| Risk Level | Invoice Count | % of Total |
|---|---|---|
| HIGH (score >= 40) | 253 | 1.2% |
| MEDIUM (score 20-39) | 741 | 3.5% |
| LOW (score < 20) | 19,957 | 95.3% |

## 4. Potential Leakage (Flagged): True Positive vs False Positive Split

The column `potential_leakage_inr` in `scored_invoices.csv` is the system's best estimate of financial risk before human review. This table splits that amount into what comes from invoices that ARE confirmed anomalies (true positives) versus invoices that are NOT anomalies (false positives). False-positive amounts would be removed after an auditor reviews and clears those invoices.

| Source | Amount (Rs) | Notes |
|---|---|---|
| From true positives (in GT) | 89,664,738.99 | Genuine leakage found by the rules |
| From false positives (not in GT) | 65,562,767.36 | Would be dropped after audit review |
| **Total potential leakage (flagged)** | **155,227,506.35** | Pre-review figure shown in dashboard |
| Exposure at risk (split/round/weekend) | 88,276,281.99 | Separate category; auditor must investigate |

For reference, the total injected extra amount in ground_truth is Rs 108,323,466.74. The true-positive leakage found (Rs 89,664,738.99) is 82.8% of this, meaning the rules recover most of the injected exposure.

**Injected extra amount by anomaly type (from ground_truth):**

| Anomaly type | Injected extra (Rs) |
|---|---|
| duplicate | 16,439,588.06 |
| outlier | 70,923,170.88 |
| round_figure | 0.00 |
| split_invoice | 20,960,707.80 |
| weekend_holiday | 0.00 |

Note: `weekend_holiday` and `round_figure` have injected_extra_amount = 0 by design (they are exposure at risk, not confirmed leakage). `split_invoice` entries record each invoice's own amount; the cluster total is tracked via group_id in ground_truth.

## 5. Honest Assessment: Limitations and Imperfect Rules

The following analysis uses real numbers from this run. These are genuine limitations that would be more severe on real data.

### Rule 1: Duplicates

- Flagged: 177 | TP: 160 | FP: 17 | FN: 0
- Precision: 90.4% | Recall: 100.0% | F1: 95.0%

**Precision 90.4%**: 17 false positives remain after the symmetry fix. All are from retainer vendors (VND0037, VND0050, VND0095, VND0123, etc.) whose dynamic-granularity rounding occasionally produces the same amount twice within 7 days for the same vendor. In real data, an invoice reference number would eliminate most of these. **Recall 100.0%**: all 160 injected duplicates found.

### Rule 2: Split Invoices

- Flagged: 489 | TP: 471 | FP: 18 | FN: 0
- Precision: 96.3% | Recall: 100.0% | F1: 98.1%

**Precision 96.3%**: 18 FPs from normal invoices that fall near the approval-limit band (80-98% of Rs 50,000) for the same vendor and approver within 3 days. **Recall 100.0%**: 0 injected clusters missed -- the pairwise SQL join only detects clusters where at least one qualifying pair exists within the 3-day window.

### Rule 3: Weekend/Holiday

- Flagged: 591 | TP: 160 | FP: 431 | FN: 0
- Precision: 27.1% | Recall: 100.0% | F1: 42.6%

**Precision 27.1%**: 431 FPs are normal invoices whose payment_date naturally falls on a weekend (the ~2% weekend noise in the data) or a holiday. With ~18,800 non-anomaly invoices and ~2% weekend noise, approximately 376 normal invoices are expected to fall on weekends. **Recall 100.0%**: all 160 injected weekend payments found.

### Rule 4: Round Figure

- Flagged: 200 | TP: 160 | FP: 40 | FN: 0
- Precision: 80.0% | Recall: 100.0% | F1: 88.9%

**Precision 80.0%**: 40 FPs are normal invoices with a round amount from non-retainer vendors whose round-rate is below 15%. **Recall 100.0%**: all 160 injected round-figure invoices found; the Rs 20,000 floor matches the 2x-9x injection range.

### Rule 5: Outliers

- Flagged: 392 | TP: 156 | FP: 236 | FN: 4
- Precision: 39.8% | Recall: 97.5% | F1: 56.5%

**Precision 39.8%**: 236 FPs are normal invoices that naturally exceed their vendor mean + 3*std (lognormal heavy upper tail). **Recall 97.5%**: 4 injected outliers missed -- for high-variance vendors, even 4x-mean can fall below mean+3*std. A more robust rule would use median + 3*MAD instead of mean + 3*std.

### Why recall is generally high

Recall figures are high partly because the data is synthetic and anomalies were injected to match rule conditions exactly (equal amounts for duplicates, exact 80-98% band for splits, exact multiples for round figures). On real data, anomalies are messier and recall would likely be 20-40 percentage points lower.

### Why precision is low for Rules 3 and 5

Rule 3 (Weekend/Holiday): ~400 normal invoices naturally fall on weekends due to the 15-45 day payment lag (deliberate noise). Better timestamp resolution (hour of day, automated vs manual) would improve precision. Rule 5 (Outliers): normal invoices for high-variance vendors exceed mean+3*std due to the lognormal distribution's heavy upper tail. A per-vendor percentile threshold (e.g. 99th percentile) would reduce FPs.

---
*Generated by src/evaluate.py. Do not tune data or rules to improve these scores -- the purpose is honest measurement, not optimisation.*