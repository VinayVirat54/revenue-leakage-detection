# POWERBI_GUIDE.md
## How to Connect Power BI Desktop to the Revenue Leakage Detection Exports

This guide walks through importing the star-schema CSVs, defining
relationships, creating the three required DAX measures, and building
the five recommended visuals.

---

## Step 1: Import the CSV Files

1. Open **Power BI Desktop**.
2. Click **Home > Get data > Text/CSV**.
3. Import each of the four files from the `powerbi_export/` folder in this
   project. Repeat for each file:

   | File | Role |
   |---|---|
   | `fact_invoices.csv` | Fact table |
   | `dim_vendor.csv` | Vendor dimension |
   | `dim_date.csv` | Date dimension |
   | `rule_summary.csv` | Rule metrics (standalone) |

4. In the preview dialog, click **Load** (not Transform) for each file.
   Power BI will detect column types automatically.

5. After loading, open **Power Query Editor** (Home > Transform data) and
   confirm these column types if Power BI did not detect them:

   | Column | Expected type |
   |---|---|
   | `fact_invoices[date_key]` | Whole Number |
   | `fact_invoices[amount_inr]` | Decimal Number |
   | `fact_invoices[risk_score]` | Whole Number |
   | `fact_invoices[potential_leakage_inr]` | Decimal Number |
   | `fact_invoices[exposure_at_risk_inr]` | Decimal Number |
   | `dim_date[date]` | Date |
   | `dim_date[date_key]` | Whole Number |
   | `dim_date[is_weekend]` | Whole Number |

6. Click **Close & Apply**.

---

## Step 2: Define Relationships (Model View)

Click the **Model view** icon (three linked rectangles on the left sidebar).

Create the following relationships by dragging the key column from one
table onto the matching column in another:

| From (fact) | To (dimension) | Cardinality | Direction |
|---|---|---|---|
| `fact_invoices[vendor_id]` | `dim_vendor[vendor_id]` | Many to One | Single |
| `fact_invoices[date_key]` | `dim_date[date_key]` | Many to One | Single |

`rule_summary` is standalone (no relationship needed).

---

## Step 3: Create DAX Measures

In the **Data view**, select `fact_invoices` in the Fields pane, then click
**New measure** for each of the three measures below.

### Measure 1: Potential Leakage (Rs)

```
Potential Leakage =
SUM( fact_invoices[potential_leakage_inr] )
```

This is the pre-review leakage figure. It includes false positives;
an auditor review would reduce it.

### Measure 2: Exposure at Risk (Rs)

```
Exposure at Risk =
SUM( fact_invoices[exposure_at_risk_inr] )
```

Invoices flagged only by split, round-figure, or weekend rules. These
need investigation but are not confirmed losses.

### Measure 3: Flag Rate %

```
Flag Rate % =
DIVIDE(
    COUNTROWS( FILTER( fact_invoices, fact_invoices[flag_count] > 0 ) ),
    COUNTROWS( fact_invoices ),
    0
) * 100
```

The percentage of invoices flagged by at least one rule. Use in a
Card visual for an at-a-glance summary.

---

## Step 4: Build the 5 Recommended Visuals

### Visual 1: KPI Cards (top row)

Visual type: **Card** (one card per metric)

Drag these fields / measures onto individual Card visuals:

| Card | Field / Measure |
|---|---|
| Total Invoices | `fact_invoices[invoice_id]` (set to Count) |
| Total Spend (Rs) | `fact_invoices[amount_inr]` (set to Sum) |
| Potential Leakage | `[Potential Leakage]` measure |
| Exposure at Risk | `[Exposure at Risk]` measure |
| Flag Rate % | `[Flag Rate %]` measure |

Tip: format money cards with "Rs #,##0.00" in the Format pane >
Value > Display units = None, Custom format.

---

### Visual 2: Leakage by Rule (Clustered Bar Chart)

Visual type: **Clustered bar chart**

1. In the Fields pane, drag:
   - **Y-axis**: `rule_summary[rule]`
   - **X-axis**: `rule_summary[flagged]`
   - Add a second series on X-axis: `rule_summary[tp]`

2. In the Visualizations pane > Legend, add `rule_summary[rule]`.

3. Optional: add a second bar chart next to it showing
   `rule_summary[precision_pct]` and `rule_summary[recall_pct]`
   to show model quality at a glance.

---

### Visual 3: Monthly Flagged Amount Trend (Line Chart)

Visual type: **Line chart**

1. Drag:
   - **X-axis**: `dim_date[date]` (set hierarchy to Month level)
   - **Y-axis**: `[Potential Leakage]` measure
   - **Secondary Y-axis**: `[Exposure at Risk]` measure

2. In the Filters pane, filter `fact_invoices[flag_count]` to > 0
   so only flagged invoices contribute to the trend.

3. Optionally add `dim_date[financial_year]` as a slicer to filter
   the trend by Indian financial year.

---

### Visual 4: Top Risky Vendors (Table)

Visual type: **Table**

Drag these columns/measures into the Values area:

| Column / Measure | Source |
|---|---|
| `dim_vendor[vendor_name]` | dim_vendor |
| Count of flagged invoices | `fact_invoices[flag_count]` > 0, Count |
| Avg risk score | `fact_invoices[risk_score]`, Average |
| `[Potential Leakage]` | measure |
| `[Exposure at Risk]` | measure |

Sort descending by Potential Leakage. Use **Top N filter** (Filters pane >
Top N > 10) on `dim_vendor[vendor_name]` to show only the top 10.

---

### Visual 5: Risk Level Distribution (Donut / Pie Chart)

Visual type: **Donut chart**

1. Drag:
   - **Legend**: `fact_invoices[risk_level]`
   - **Values**: `fact_invoices[invoice_id]` (Count)

2. In Format pane, set the color for each slice:
   - HIGH: red (#D32F2F)
   - MEDIUM: orange (#F57C00)
   - LOW: green (#388E3C)

3. Place next to the KPI cards at the top of the report page.

---

## Step 5: Publish and Schedule Refresh (optional)

1. Click **File > Publish > Publish to Power BI**.
2. In the Power BI service, go to the dataset settings and configure
   a **Scheduled refresh** to re-import the CSVs after each pipeline run
   (run `score.py` then `export_powerbi.py` to regenerate the files).

---

## Notes

- All amounts are in Indian Rupees (INR).
- `potential_leakage_inr` is a **pre-review** figure. It includes false
  positives from Rules 1 and 5. After an auditor clears the FP invoices,
  the true confirmed leakage will be lower.
- `exposure_at_risk_inr` covers invoices flagged only by split, round-figure,
  or weekend rules. These need investigation but are not confirmed losses.
- See `outputs/evaluation_report.md` for precision and recall per rule.
- The data in `powerbi_export/` is regenerated by running:
  ```
  python src/score.py
  python src/evaluate.py
  python src/export_powerbi.py
  ```
