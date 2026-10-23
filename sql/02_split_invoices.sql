-- =============================================================================
-- 02_split_invoices.sql
-- =============================================================================
--
-- WHAT IT DETECTS
-- ---------------
-- Clusters of invoices from the same vendor and approver where:
--   (a) each individual invoice amount is between 80% and 100% (exclusive)
--       of the approval limit (:approval_limit), and
--   (b) the invoices fall within a 3-day window of each other, and
--   (c) the combined total of the cluster exceeds the approval limit.
-- All invoices in such a cluster are flagged.
--
-- WHY IT MATTERS TO AN AUDITOR
-- ----------------------------
-- When a single purchase exceeds the approval limit it requires sign-off
-- from a senior manager or a committee.  A common fraud technique is to
-- deliberately split one large purchase into several smaller invoices, each
-- just below the threshold, so that no individual invoice triggers the extra
-- approval step.  This is sometimes called "invoice splitting" or
-- "structuring" (borrowing the term from anti-money-laundering).  Auditors
-- flag it because it circumvents the internal control that exists to prevent
-- unauthorised large expenditures.
--
-- KNOWN WEAKNESS
-- --------------
-- The 3-day window and the 80%-100% band are heuristics.  A sophisticated
-- fraudster could spread invoices over a longer period or use amounts outside
-- this band.  Conversely, a vendor who legitimately delivers multiple small
-- items within a few days will produce false positives.  The rule also
-- requires the same approver_id across the cluster; if a fraudster uses
-- different approvers the cluster will not be detected.
-- =============================================================================

WITH clusters AS (
    -- Find pairs of invoices from the same vendor + approver within 3 days,
    -- each amount in the suspicious band (80% to <100% of approval limit).
    SELECT
        a.vendor_id,
        a.approver_id,
        a.invoice_id  AS inv_a,
        b.invoice_id  AS inv_b,
        a.amount_inr  AS amt_a,
        b.amount_inr  AS amt_b,
        a.invoice_date AS date_a,
        b.invoice_date AS date_b
    FROM invoices a
    JOIN invoices b
        ON  a.vendor_id   = b.vendor_id
        AND a.approver_id = b.approver_id
        AND a.invoice_id  < b.invoice_id   -- avoid counting the same pair twice
        AND a.amount_inr  >= :approval_limit * 0.80
        AND a.amount_inr  <  :approval_limit * 1.00
        AND b.amount_inr  >= :approval_limit * 0.80
        AND b.amount_inr  <  :approval_limit * 1.00
        AND ABS(julianday(b.invoice_date) - julianday(a.invoice_date)) <= 3
    WHERE (a.amount_inr + b.amount_inr) > :approval_limit
),
-- Collect all distinct invoice_ids that appear in at least one qualifying cluster
flagged_ids AS (
    SELECT inv_a AS invoice_id FROM clusters
    UNION
    SELECT inv_b AS invoice_id FROM clusters
)
SELECT
    i.invoice_id,
    'Split invoice: vendor ' || i.vendor_id
        || ', approver ' || i.approver_id
        || ', amount ' || CAST(CAST(i.amount_inr AS INTEGER) AS TEXT)
        || ' (near approval limit)' AS reason
FROM invoices i
JOIN flagged_ids f ON i.invoice_id = f.invoice_id
ORDER BY i.invoice_id
