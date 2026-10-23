# EXPLAINING.md
## Plain-Language Explanation of the Project

This document explains every technical decision in plain English,
documents the bugs found and fixed during development, and provides
model answers to 15 likely interview questions.

---

## Part 1: How the Data Generator Works

`src/generate_data.py` creates fake but realistic invoice data with
seed=42 so every run produces identical files.

**Normal invoices (20,000 rows)**

- 150 vendors, each with their own typical billing amount. The amounts
  are drawn from a lognormal distribution -- this means most invoices
  cluster around a vendor's typical value, but occasionally there are
  larger ones. This matches real accounts-payable data.
- About 10% of vendors are "retainer" vendors (rent, maintenance,
  software licences). Their amounts are rounded to a dynamic granularity
  of about 1% of the draw value so they look stable, not jagged.
- Payment dates are invoice date + 15 to 45 days, always snapped to a
  weekday. Then a 2% coin-flip moves some payments to a weekend, giving
  the weekend rule realistic false positives.

**Injected anomalies (1,111 rows total)**

Five types, each labelled in `data/ground_truth.csv`:

| Type | How injected | New rows? |
|---|---|---|
| duplicate | New invoice, same vendor+amount, invoice_date +1-7 days | Yes (160) |
| split_invoice | New cluster of 2-4 invoices, each 80-98% of Rs 50,000 | Yes (471) |
| weekend_holiday | Existing normal invoice: payment_date mutated to Sat/Sun | No (mutated) |
| round_figure | New invoice, amount = 2x-9x * Rs 10,000, non-retainer vendor | Yes (160) |
| outlier | New invoice, amount = 4x-8x the vendor's mean amount | Yes (160) |

Weekend anomalies mutate existing rows rather than adding new ones.
This was a deliberate design choice: it keeps the invoice count clean
and avoids the problem where the duplicate rule would flag the original
and the copy together.

---

## Part 2: Each SQL Rule Explained

### Rule 1: Duplicates (`sql/01_duplicates.sql`)

**What it detects:** Two invoices from the same vendor for the exact
same rupee amount where the invoice dates are within 7 days. It flags
the later invoice (the suspected double payment).

**Why it matters to an auditor:** Duplicate payments are the most
common type of confirmed cash loss in accounts-payable audits. The
recovery is straightforward: contact the vendor and request a refund.

**How the SQL works:** Self-join on (vendor_id, amount_inr). The join
condition has two cases: (1) b's date is strictly later than a's and
within 7 days, or (2) dates are the same and b has the higher invoice_id
(tiebreaker). This ensures each pair is visited exactly once and only
one invoice per pair is flagged.

**Known weakness:** No invoice reference number. If a vendor legitimately
bills the same fixed fee twice in one week (retainer overlap), it gets
flagged. On real data, joining on the vendor's own invoice number would
eliminate most of these false positives.

---

### Rule 2: Split Invoices (`sql/02_split_invoices.sql`)

**What it detects:** Clusters of invoices from the same vendor and
approver, all within 3 days, where each invoice is between 80% and 98%
of the approval limit (Rs 50,000) and the cluster total exceeds it.

**Why it matters to an auditor:** When a purchase exceeds the approval
limit, it needs a senior manager's sign-off. A common fraud technique
is to split one large purchase into several smaller invoices, each just
below the threshold, to bypass that control. This is called "structuring"
in anti-money-laundering literature.

**How the SQL works:** A CTE finds all qualifying invoice pairs (same
vendor, same approver, within 3 days, both in the 80-98% band, combined
total > limit). It then collects all invoice_ids that appear in at least
one such pair. This catches 2-invoice clusters directly; larger clusters
are caught if any 2-invoice subset within them qualifies.

**Known weakness:** The 3-day window and 80-98% band are heuristics.
A sophisticated fraudster could spread invoices over a longer period.
Also, the rule requires the same approver across the cluster; different
approvers per invoice would bypass detection.

---

### Rule 3: Weekend/Holiday (`sql/03_weekend_holiday.sql`)

**What it detects:** Invoices whose payment_date falls on a Saturday,
Sunday, or a date in the holidays table.

**Why it matters to an auditor:** Legitimate payments almost always
happen on working days. A payment on a weekend or public holiday is
suspicious because the authorisation controls that prevent fraud are
typically unavailable (no second approver, no manager oversight).

**How the SQL works:** Uses SQLite's `strftime('%w', date)` function,
which returns 0 for Sunday and 6 for Saturday. A LEFT JOIN with the
holidays table catches public holidays.

**Known weakness:** About 2% of normal payments naturally fall on weekends
because of the 15-45 day payment lag. This rule has the lowest precision
(27.1%) as a result. Better data (payment timestamp, payment method
metadata) would help distinguish automated standing orders from manual
suspicious payments.

---

### Rule 4: Round Figure (`sql/04_round_figure.sql`)

**What it detects:** Invoices where the amount is an exact multiple of
Rs 10,000 and at least Rs 20,000, but only for vendors whose overall
round-figure rate is below 15%.

**Why it matters to an auditor:** Real invoices have irregular amounts
(taxes, line items, etc.). A perfectly round amount can indicate the
invoice was created retrospectively to justify a payment, or was estimated
rather than calculated.

**How the SQL works:** A CTE computes each vendor's round-figure rate
(number of invoices with amounts that are multiples of 10,000 / total
invoices). The main query filters on both the amount condition and
rate < 15%, so retainer vendors who naturally bill round amounts are
excluded.

**Known weakness:** The 15% threshold is a heuristic. New vendors with
few invoices may accidentally have a high round-rate even if they are
legitimate. The Rs 20,000 floor means small round amounts (e.g. Rs 10,000
for a minor service) are not flagged.

---

### Rule 5: Outliers (`sql/05_outliers.sql`)

**What it detects:** Invoices whose amount is more than 3 standard
deviations above that vendor's own mean, for vendors with at least
10 invoices.

**Why it matters to an auditor:** Every vendor has a "normal" billing
range. A sudden spike could mean overbilling, a data-entry error (extra
zero), or an unauthorised large purchase. Outlier invoices often represent
the largest individual exposure amounts.

**How the SQL works:** SQLite has no built-in STDDEV function, so we
compute it manually using the identity: std = sqrt(avg(x^2) - avg(x)^2).
This is population standard deviation. The CTE also enforces a minimum
of 10 invoices so the baseline is meaningful.

**Known weakness:** The mean+3*std threshold assumes roughly normal
amounts, but lognormal distributions have heavier upper tails. This
produces false positives for high-variance vendors. A more robust rule
would use median + 3 * MAD (median absolute deviation).

---

## Part 3: Scoring Logic

Each rule has a weight from config.yaml:

| Rule | Weight |
|---|---|
| Duplicate | 40 |
| Split invoice | 30 |
| Outlier | 20 |
| Round figure | 10 |
| Weekend/holiday | 10 |

An invoice's `risk_score` = sum of weights for all rules that fired,
capped at 100. Thresholds: HIGH >= 40, MEDIUM >= 20, LOW < 20.

The duplicate weight is highest because it is the strongest signal for
confirmed cash loss. Weekend/holiday and round figure are lowest because
they have the most false positives.

---

## Part 4: Potential Leakage vs Exposure at Risk

These two concepts come from MY_DESIGN.md and reflect the difference
between "we are fairly sure this money leaked" vs "this is suspicious
and needs investigation".

**Potential leakage (flagged):**
- Duplicate-flagged invoice: full invoice amount (the second payment
  is a direct loss).
- Outlier-flagged invoice (not also a duplicate): the excess above the
  vendor's mean (the amount above normal billing).
- If both duplicate and outlier fire on the same invoice, only the
  duplicate amount is counted to avoid double-counting.
- This figure includes false positives. After an auditor reviews and
  clears the FPs, the true amount would be lower.

**Exposure at risk:**
- Full invoice amount for invoices flagged ONLY by split, round figure,
  or weekend -- but NOT by duplicate or outlier.
- These are suspicious but not confirmed losses. An auditor must
  investigate each one.
- There is no overlap with potential leakage by construction.

In the current run: potential leakage = Rs 15.52 Cr (Rs 8.97 Cr from
true positives), exposure at risk = Rs 8.83 Cr.

---

## Part 5: How to Read Precision and Recall

**Precision:** Of all the invoices the rule flagged, what fraction were
genuine anomalies? High precision = few false alarms.

**Recall:** Of all the genuine anomalies in the data, what fraction did
the rule catch? High recall = few missed frauds.

**F1:** The harmonic mean of precision and recall. Balances both.

Example from this project:
- Rule 3 (Weekend): precision 27.1%, recall 100%. It catches every
  injected anomaly but also flags 431 innocent invoices.
- Rule 1 (Duplicates): precision 90.4%, recall 100%. Far fewer false
  alarms but still 17 retainer-vendor coincidences.

In a real audit setting, low precision means auditors waste time
reviewing false alarms. Low recall means real fraud goes undetected.
The right balance depends on audit capacity and risk appetite.

---

## Part 6: Bugs Found and Fixed During Development

### Bug 1: Retainer rounding causing mass duplicate false positives

**What happened:** Retainer vendors had amounts rounded to the nearest
Rs 10,000. A vendor with 60 invoices at exactly Rs 10,000 produced
hundreds of same-vendor same-amount pairs within 7 days. The duplicate
rule flagged 750 invoices instead of ~177.

**Root cause:** `round(x / 10000) * 10000` collapses all lognormal draws
below ~Rs 15,000 to exactly Rs 10,000. With 60 identical-amount invoices
over 3 years, pairs within any 7-day window are inevitable.

**Fix:** Changed retainer vendors to use dynamic-granularity rounding
(~1% of the draw value, minimum Rs 100). This gives stable-ish amounts
like Rs 47,500 or Rs 8,400 instead of always Rs 10,000. Within-7-day
normal pairs dropped from 129 to 17.

**Lesson:** When injecting noise into data, be precise about the
statistical distribution. Fixed-bucket rounding creates artificial spikes
in the amount distribution that interact badly with detection rules.

---

### Bug 2: Duplicate SQL flagging both sides of same-date pairs (symmetry bug)

**What happened:** The original SQL used `b.invoice_date >= a.invoice_date`
as the condition for b being the "later" invoice. When a = b on the same
date, this condition is true in BOTH directions: both (a->b) and (b->a)
satisfy it. So both invoices in a same-date pair were flagged.

The weekend injection at the time created 160 anomalous invoices that
were copies of normal invoices with the same invoice_date and amount.
This caused 320 extra false positives from the symmetry alone.

**Root cause:** `>=` is not a strict total order. It does not uniquely
identify which invoice is "later" when dates are equal.

**Fix (two parts):**
1. Changed the duplicate injection so the duplicate invoice always has
   invoice_date = original + 1-7 days (never same-date). This eliminated
   the source of same-date duplicates.
2. Changed the SQL to use a strict two-case condition: either b's date is
   strictly greater than a's, or dates are equal and a.invoice_id < b.invoice_id
   (tiebreaker by ID, flagging only the higher ID). This makes same-date
   pairs deterministic and flags only one side.

**Lesson:** Self-joins on non-strict inequalities produce symmetric
matches. Always use a strict total order to ensure each pair is visited
exactly once.

---

### Bug 3: Weekend injection creating copied invoice rows (source of symmetry FPs)

**What happened:** The original weekend injection created 160 new invoice
rows by copying normal invoices and changing only the payment_date. These
copies had the same vendor_id, amount_inr, and invoice_date as their
source normal invoice. The duplicate rule then flagged both the copy and
the original (same-date, same-vendor, same-amount pair).

**Fix:** Changed weekend injection to mutate 160 existing normal invoices
in-place (only payment_date changes). No new invoice rows are created.
Total invoice count stays at 20,000 base rows + only the truly new
anomaly types (duplicates, splits, round_figures, outliers).

**Lesson:** When modelling "suspicious payment timing", the invoice itself
is not a duplicate -- only the payment timing is wrong. Keeping the
invoice row intact and changing only payment_date is both more realistic
and avoids cross-rule interference.

---

## Part 7: 15 Interview Questions with Model Answers

---

**Q1. Why synthetic data?**

Because real company invoice data is confidential and I don't have access
to it. Synthetic data lets me control exactly what anomalies exist and
measure how well the rules detect them. The trade-off is that the rules
were designed to match the data, so evaluation scores are optimistic. I
document this limitation clearly in the evaluation report and README.

---

**Q2. Why do your results look so good? Recall is near 100% for most rules.**

Because the anomalies were injected to match the rule conditions exactly.
Duplicate invoices have exactly the same amount; split invoices are exactly
in the 80-98% band; round figures are exact multiples of 10,000. Real fraud
is messier: amounts may differ slightly, invoice dates may be further apart,
and amounts may not fall neatly in the detection band. I estimate recall
would be 20-40 percentage points lower on real production data. This
limitation is stated explicitly in the evaluation report.

---

**Q3. Why is Rule 3 (Weekend/Holiday) precision only 27.1%?**

Because about 2% of normal invoices naturally have a payment date on a
weekend. With a 15-45 day payment lag from invoice date to payment date,
some invoices randomly land on Saturdays or Sundays even though they are
legitimate. With ~18,800 normal invoices, that is roughly 376 natural
weekend payments. These cannot be distinguished from suspicious ones
without payment timestamps (hour of day) or ERP metadata about whether
the payment was automated or manually approved.

---

**Q4. How would this work on real data?**

Several changes would be needed. First, the data pipeline would connect
to the actual ERP system via API or database query instead of generating
synthetic data. Second, the rules would need calibration: the approval
limit, the duplicate window, and the round-figure threshold would be set
to the company's actual policy. Third, a vendor whitelist would be needed
to exclude retainer vendors from the duplicate and round-figure rules.
Fourth, precision would be lower and an auditor would need to review flagged
invoices and provide feedback to tune the thresholds over time.

---

**Q5. What false positives would an auditor see?**

Three main types:

1. Retainer vendors with recurring similar amounts flagged by Rule 1
   (duplicates). About 17 in this dataset -- all legitimate monthly fees.
2. Normal invoices that naturally fall on a weekend flagged by Rule 3.
   About 431 false positives from the ~2% natural weekend rate.
3. High-variance vendors whose occasional large-but-legitimate invoices
   exceed the mean+3*std threshold for Rule 5 (outliers). About 236 FPs.

An auditor should see these alongside the rule that fired and the reason
column, which explains the specific signal (e.g. "gap 4 days, same amount
Rs 25,000 as INV000831").

---

**Q6. Did you use AI?**

Yes. I used Kiro (an AI coding assistant) to write and debug the code.
The problem framing, rule design, and analysis decisions are mine. Kiro
helped me implement the SQL queries, Python scripts, and tests, and
pointed out bugs like the symmetry problem in the duplicate SQL. I reviewed
every piece of code and understood what it does before accepting it. This
is stated in the README.

---

**Q7. How does your split-invoice SQL work?**

The SQL uses a CTE called `clusters` that self-joins the invoices table
to find all pairs of invoices from the same vendor and approver where both
amounts are in the 80-98% band, the dates are within 3 days, and the
combined total exceeds the approval limit. The key line is
`a.invoice_id < b.invoice_id` which ensures each pair is only counted
once. A second CTE collects all invoice_ids that appear in at least one
qualifying pair (as either side). The final SELECT joins back to the
invoices table to get the full row for output. This approach correctly
handles clusters of 3 or 4 invoices as long as at least one qualifying
2-invoice pair exists within the cluster.

---

**Q8. What would you improve?**

Several things:

1. Replace mean+3*std with median+3*MAD for Rule 5 to reduce false
   positives on high-variance vendors.
2. Add an invoice reference number column and use it in Rule 1 to
   distinguish genuine duplicates from retainer-vendor coincidences.
3. Extend the holiday list to cover state-specific and bank holidays.
4. Add a payment-timing rule: flag invoices where the approval timestamp
   was outside business hours (requires timestamp data).
5. Build a second-stage ML model (e.g. isolation forest) that uses the
   5 rule flags plus other features (vendor age, payment mode, department)
   to produce a more precise risk score.
6. Add a feedback loop: auditors mark each flagged invoice as true or false
   positive; these labels are used to retrain the ML model.

---

**Q9. Why did you use SQLite instead of a proper database?**

SQLite is file-based, requires no server setup, and is portable across
all platforms. For a portfolio project with 20,000 invoices, SQLite is
fast enough and keeps the project entirely self-contained. On real data
at enterprise scale, the same SQL queries would run on PostgreSQL, Snowflake,
or BigQuery with minimal changes -- only the connection string and date
functions would differ.

---

**Q10. Why not just use pandas for the detection rules?**

The SQL approach has three advantages. First, it is the language auditors
and finance teams already understand -- a rule written as SQL can be shown
to and verified by a non-programmer. Second, SQL scales naturally to
larger databases; the same query works whether the table has 20,000 rows
or 20 million. Third, each rule is in a separate file with a comment block
explaining what it detects, why it matters, and its weakness -- this makes
the detection logic auditable and maintainable.

---

**Q11. How did you verify correctness of the pipeline?**

Three layers of verification:

1. The data generator injects known anomalies and writes their IDs to
   `ground_truth.csv`. `evaluate.py` computes TP/FP/FN per rule by
   comparing flags against this ground truth.
2. `score.py` runs 6 self-checks after every run: row count, no duplicate
   IDs, score cap, level thresholds, no leakage/exposure overlap, and
   vendor totals reconciling with invoice totals.
3. 41 pytest tests cover each SQL rule (flag / no-flag / edge cases),
   the scoring logic, and reproducibility (same seed gives same files).

---

**Q12. How did you calculate potential leakage?**

For duplicate-flagged invoices: the full invoice amount, because the
second payment is a direct double-payment and the entire amount is
theoretically recoverable.

For outlier-flagged invoices (not also duplicates): the excess above
the vendor's mean amount, because only the part above normal is potentially
overbilling.

If both duplicate and outlier fire on the same invoice, only the duplicate
amount is counted to avoid double-counting.

Split, round-figure, and weekend invoices go into "exposure at risk"
instead, because these patterns indicate suspicious behaviour but do not
by themselves confirm a loss. The two categories have no overlap by
construction.

---

**Q13. What is the approval limit and why does it matter?**

Rs 50,000, stored in config.yaml. This is the threshold above which an
invoice requires sign-off from a senior manager. The split-invoice rule
specifically targets invoices that cluster just below this threshold, which
is the signature of an approval-bypass attempt. The limit is configurable
so the system can be adapted to any company's policy without changing code.

---

**Q14. Your ground truth has 1,111 rows but your total anomaly invoices
are also 1,111. Is that by design?**

Yes. One of the explicit design requirements is one row per anomalous
invoice in ground_truth.csv. For weekend_holiday anomalies, the existing
normal invoice is mutated in-place (payment_date changed) and labelled in
ground_truth -- no new row is created in invoices.csv. For duplicate
anomalies, only the NEW (later) invoice is labelled, not the original.
For split clusters, every invoice in the cluster gets its own ground_truth
row with a shared group_id. This design makes it straightforward to
compute invoice-level precision and recall: for each rule, the set of
invoice_ids it flags is compared directly against the set of invoice_ids
of that anomaly type in ground_truth.

---

**Q15. What did the bugs you found teach you about data engineering?**

Three lessons:

1. **Rounding is not neutral.** Fixed-bucket rounding (to nearest 10,000)
   creates artificial spikes in a distribution that interact badly with
   similarity-based detection rules. Always think about the second-order
   effects of any discretisation decision.

2. **Self-joins need strict total orders.** Using >= in a self-join means
   both (a, b) and (b, a) satisfy the condition when a = b on the key.
   Always use either a strict inequality or an explicit tiebreaker to make
   each pair unique.

3. **Anomaly injection and detection rules are coupled.** When the injection
   of one anomaly type (weekend: copy a row, change payment_date) shares
   columns with another rule's detection condition (duplicate: same vendor,
   same amount, same invoice_date), you get cross-rule interference. The
   fix was to inject the anomaly at the right level: mutate only what is
   anomalous (payment_date) rather than copying the entire row.
