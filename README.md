# Revenue Leakage Detection

An audit-analytics portfolio project that automatically detects suspicious
vendor invoice patterns in accounts-payable data using SQL rules, risk
scoring, and an interactive Streamlit dashboard.

Data is synthetic. Built with AI assistance (Kiro); problem framing,
rule design and analysis decisions are mine.

---

## Problem

A mid-size company pays thousands of vendor invoices every year. Some
invoices are duplicate payments, approval-limit bypasses, or statistical
outliers that quietly drain cash. Manual review is too slow and too
incomplete to catch all of them. This project flags suspicious invoices
automatically and quantifies the financial exposure.

---

## Approach

1. **Synthetic data generation** -- 20,951 invoices across 150 vendors
   over 3 Indian financial years (FY 2022-23 to FY 2024-25), with five
   types of anomalies injected so evaluation is possible.

2. **SQL detection rules** -- five separate `.sql` files, each targeting
   one pattern. Rules run against a SQLite database.

3. **Risk scoring** -- each rule has a weight; an invoice's score is the
   sum of weights of triggered rules, capped at 100. Score >= 40 = HIGH.

4. **Evaluation** -- flags compared against `data/ground_truth.csv` to
   compute precision, recall and F1 per rule.

5. **Dashboard** -- Streamlit app with KPI cards, charts, top-vendor
   and top-invoice tables, and a model-quality tab.

6. **Power BI export** -- star-schema CSVs for independent analysis.

---

## Folder Structure

```
revenue-leakage-detection/
|-- app.py                    Streamlit dashboard (run from root)
|-- config.yaml               Approval limit, rule weights, thresholds
|-- DATA_DICTIONARY.md        Column definitions for all datasets
|-- MY_DESIGN.md              Original design decisions (Hindi/English)
|-- EXPLAINING.md             Plain-language explanation + interview Q&A
|-- POWERBI_GUIDE.md          Step-by-step Power BI Desktop guide
|-- README.md                 This file
|-- requirements.txt          Pinned Python dependencies
|-- .gitignore
|
|-- data/
|   |-- raw/invoices.csv      20,951 synthetic invoices
|   |-- ground_truth.csv      1,111 anomaly labels (one row per anomaly)
|   |-- holidays.csv          40 Indian national holidays (FY22-25)
|   `-- db/invoices.db        SQLite database (regenerated; not in git)
|
|-- sql/
|   |-- 01_duplicates.sql
|   |-- 02_split_invoices.sql
|   |-- 03_weekend_holiday.sql
|   |-- 04_round_figure.sql
|   `-- 05_outliers.sql
|
|-- src/
|   |-- generate_data.py      Stage A: synthetic data generator
|   |-- load_db.py            Stage B: CSV -> SQLite loader
|   |-- run_rules.py          Stage B: run rules, print counts
|   |-- score.py              Stage C: scoring + vendor summary
|   |-- evaluate.py           Stage C: precision/recall/F1
|   `-- export_powerbi.py     Stage D: star-schema CSV export
|
|-- outputs/
|   |-- scored_invoices.csv   Invoice-level scores and flags
|   |-- vendor_risk.csv       Vendor-level risk summary
|   |-- evaluation_metrics.csv Per-rule precision/recall/F1
|   `-- evaluation_report.md  Full evaluation with limitations
|
|-- powerbi_export/
|   |-- fact_invoices.csv
|   |-- dim_vendor.csv
|   |-- dim_date.csv
|   `-- rule_summary.csv
|
`-- tests/
    |-- test_rules.py         SQL rule unit tests (26 tests)
    `-- test_score.py         Scoring logic tests (15 tests)
```

---

## Run Commands (in order)

All commands run from the project root.

```bash
# 1. Generate synthetic data
python src/generate_data.py

# 2. Load into SQLite
python src/load_db.py

# 3. Run detection rules (sanity check output)
python src/run_rules.py

# 4. Score invoices and build vendor table
python src/score.py

# 5. Evaluate against ground truth
python src/evaluate.py

# 6. Export Power BI files
python src/export_powerbi.py

# 7. Launch Streamlit dashboard
streamlit run app.py

# 8. Run tests
python -m pytest tests/ -v
```

---

## Results

All numbers are from `outputs/evaluation_report.md` produced by running
the pipeline above.

### Per-Rule Detection Metrics

| Rule | Flagged | GT Count | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Rule 1: Duplicates | 177 | 160 | 160 | 17 | 0 | 90.4% | 100.0% | 95.0% |
| Rule 2: Split Invoices | 489 | 471 | 471 | 18 | 0 | 96.3% | 100.0% | 98.1% |
| Rule 3: Weekend/Holiday | 591 | 160 | 160 | 431 | 0 | 27.1% | 100.0% | 42.6% |
| Rule 4: Round Figure | 200 | 160 | 160 | 40 | 0 | 80.0% | 100.0% | 88.9% |
| Rule 5: Outliers | 392 | 160 | 156 | 236 | 4 | 39.8% | 97.5% | 56.5% |
| **Overall** | **1,731** | **1,111** | **1,107** | **624** | **4** | **63.9%** | **99.6%** | **77.9%** |

### Financial Exposure

| Metric | Amount |
|---|---|
| Potential leakage (flagged, before audit review) | Rs 15.52 Cr |
| -- from true positives | Rs 8.97 Cr |
| -- from false positives (drops after audit) | Rs 6.56 Cr |
| Exposure at risk (split / round / weekend) | Rs 8.83 Cr |
| Total invoices flagged | 1,731 of 20,951 (8.3%) |
| HIGH risk (score >= 40) | 253 invoices |
| MEDIUM risk (score 20-39) | 741 invoices |

---

## Limitations

**Synthetic data.** All invoices were generated by `src/generate_data.py`.
The system has never seen real company data.

**Recall is inflated because anomalies were injected to match the rules.**
Duplicates have exactly the same amount; split invoices are exactly in the
80-98% band; round figures are exact multiples of 10,000. Real anomalies
are messier. Expect recall to be 20-40 percentage points lower on live data.

**Rule 3 (Weekend/Holiday) precision is only 27.1%.** Approximately 400
normal invoices naturally fall on a weekend because of the 15-45 day
payment lag. There is no way to distinguish these from suspicious payments
without payment timestamps (hour of day) or payment-system metadata.

**Rule 5 (Outliers) precision is only 39.8%.** The lognormal amount
distribution has a heavy upper tail, so many normal invoices for
high-variance vendors exceed the mean + 3 std threshold. A per-vendor
percentile approach would reduce false positives.

**Rule 1 (Duplicates) 17 false positives from retainer vendors.** Retainer
vendors (rent, maintenance contracts) occasionally issue invoices for the
same rounded amount within 7 days. A real system would join on the vendor's
own invoice reference number, eliminating almost all of these.

**Potential leakage includes false positives and is before audit review.**
The Rs 15.52 Cr figure is what the system flags. After an auditor reviews
each HIGH/MEDIUM invoice and clears the false positives, the confirmed
amount would be lower. The split between TP and FP leakage is shown
in `outputs/evaluation_report.md`.

**Simple rules, no machine learning.** The rules are deterministic SQL
queries. They cannot learn vendor-specific patterns, adapt to new fraud
techniques, or weight contextual signals like approver behaviour over time.

**Partial holiday list.** Only 40 central-government holidays across
FY 2022-25 are included. State-specific and bank holidays are absent,
causing both missed flags and false positives.

---

## Future Work

- Replace mean + 3*std with median + 3*MAD for a more robust outlier rule.
- Add an invoice reference number to the data and use it in the duplicate
  rule to eliminate retainer-vendor false positives.
- Extend the holiday list to cover state-specific and bank holidays.
- Add a payment-timing rule: flag invoices approved outside business hours.
- Build a simple ML model (isolation forest or logistic regression) on
  top of the rule flags as a second stage.
- Connect to a real ERP via API instead of generating synthetic data.

---

## Screenshot Placeholder

_(Add dashboard screenshots here after running `streamlit run app.py`)_

```
[Dashboard screenshot - KPI row]
[Dashboard screenshot - leakage by rule chart]
[Dashboard screenshot - top vendors table]
[Dashboard screenshot - model quality tab]
```
