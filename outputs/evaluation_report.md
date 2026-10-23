# Evaluation Report

This report evaluates the five detection rules against the injected anomalies in `data/ground_truth.csv`.  Because the dataset is **synthetic** and the anomalies were designed to match the rules, recall figures are higher than would be expected on real data.  See the Limitations section.

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

## 4. Leakage Comparison

Injected extra amount = the true financial exposure embedded in the synthetic data.  Confirmed leakage found = what score.py computed from flagged invoices.

| Metric | Amount (Rs) |
|---|---|
| Total injected extra amount (GT) | 108,323,466.74 |
| Confirmed leakage found (scored) | 155,227,506.35 |
| Exposure at risk (scored) | 88,276,281.99 |
| Recovery rate (found / injected) | 143.3% |

**Injected extra amount by anomaly type:**

| Anomaly type | Injected extra (Rs) |
|---|---|
| duplicate | 16,439,588.06 |
| outlier | 70,923,170.88 |
| round_figure | 0.00 |
| split_invoice | 20,960,707.80 |
| weekend_holiday | 0.00 |

Note: `weekend_holiday` and `round_figure` have injected_extra_amount = 0 by design (they are exposure, not confirmed leakage).  `split_invoice` entries also have injected_extra_amount = individual invoice amount; the full cluster sum is tracked via group_id.

**Why recovery rate exceeds 100%:** Confirmed leakage includes FP outlier invoices (236 normal invoices that genuinely exceed their vendor mean+3*std) and FP duplicate invoices (17 retainer-vendor coincidences). These false positives add to the confirmed leakage figure but are not in ground_truth, so the 'found' amount exceeds the 'injected' amount. In a real audit, an auditor reviewing each flagged invoice would exclude these FPs, reducing the confirmed amount to below the injected total. This is a known consequence of imperfect precision in Rules 1 and 5.

## 5. Honest Assessment: Limitations and Imperfect Rules

The following analysis uses real numbers from this run.  These are not post-hoc rationalisations -- they are genuine limitations that would be more severe on real data.

### Rule 1: Duplicates

- Flagged: 177 | TP: 160 | FP: 17 | FN: 0
- Precision: 90.4% | Recall: 100.0% | F1: 95.0%

**Precision 90.4%**: 17 false positives remain after the symmetry fix.  All are from retainer vendors (VND0037, VND0050, VND0095, VND0123, etc.) whose dynamic-granularity rounding occasionally produces the same amount twice within 7 days for the same vendor.  In real data, an invoice reference number would eliminate most of these.  **Recall 100.0%**: all 160 injected duplicates found; the SQL correctly uses a strict positive date gap so no symmetry FPs remain.

### Rule 2: Split Invoices

- Flagged: 489 | TP: 471 | FP: 18 | FN: 0
- Precision: 96.3% | Recall: 100.0% | F1: 98.1%

**Precision 96.3%**: 18 FPs come from normal invoices that happen to fall near the approval-limit band (80-98% of Rs 50,000) for the same vendor and approver within 3 days.  On real data this band would need calibration per department.  **Recall 100.0%**: 0 injected split clusters not caught -- the pairwise SQL join only detects 2-invoice clusters; clusters of 3-4 are caught only if at least one qualifying pair exists within the cluster.

### Rule 3: Weekend/Holiday

- Flagged: 591 | TP: 160 | FP: 431 | FN: 0
- Precision: 27.1% | Recall: 100.0% | F1: 42.6%

**Precision 27.1%**: 431 FPs are normal invoices whose payment_date naturally falls on a weekend (the ~2% weekend-noise we deliberately introduced) or on a public holiday date.  With 20,951 invoices and ~2% weekend noise, approximately 400 normal invoices are expected to fall on weekends.  **Recall 100.0%**: all 160 injected weekend payments found because they were placed directly on Saturday/Sunday.

### Rule 4: Round Figure

- Flagged: 200 | TP: 160 | FP: 40 | FN: 0
- Precision: 80.0% | Recall: 100.0% | F1: 88.9%

**Precision 80.0%**: 40 FPs are normal invoices with a round amount from non-retainer vendors whose round-rate happens to be below 15%.  Retainer vendors with round-to-granularity amounts occasionally hit exact 10,000 multiples.  **Recall 100.0%**: all 160 injected round-figure invoices found; the Rs 20,000 floor now matches the injection multiplier (2x to 9x), so no injected anomaly is below the threshold.

### Rule 5: Outliers

- Flagged: 392 | TP: 156 | FP: 236 | FN: 4
- Precision: 39.8% | Recall: 97.5% | F1: 56.5%

**Precision 39.8%**: 236 FPs are normal invoices that naturally exceed their vendor's mean + 3*std (high-variance vendors where the lognormal distribution has a heavy upper tail).  **Recall 97.5%**: 4 injected outliers not caught -- for high-variance vendors, even a 4x-mean multiplier may produce a z-score just below 3.0 (e.g. vendors with std > mean).  A more robust rule would use median + 3*MAD (median absolute deviation) instead of mean + 3*std.

### Why recall is generally high

Recall figures are high partly because the data is synthetic and the anomalies were injected to match the rule conditions exactly (e.g. duplicate amounts are exactly equal, split amounts are exactly in the 80-98% band).  On real data, anomalies are messier: duplicates may have small amount differences, split invoices may span longer periods, and outliers may be harder to distinguish from seasonal spikes.  Reported recall would likely be 20-40 percentage points lower on real production data.

### Why precision is low for Rules 3 and 5

Rule 3 (Weekend/Holiday) flags ~400 normal invoices that naturally fall on weekends due to the 15-45 day payment lag.  This is deliberate noise in the data to make the rule realistic.  On real data with better timestamp resolution (hour of day, automated vs manual payments), precision would improve.  Rule 5 (Outliers) flags normal invoices for high-variance vendors because their lognormal distribution has a heavy upper tail.  Using a per-vendor percentile threshold (e.g. 99th percentile) instead of mean+3*std would reduce these FPs.

---
*Generated by src/evaluate.py.  Do not tune data or rules to improve these scores -- the purpose is honest measurement, not optimisation.*