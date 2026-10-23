-- =============================================================================
-- 01_duplicates.sql
-- =============================================================================
--
-- WHAT IT DETECTS
-- ---------------
-- Two invoices from the same vendor for the exact same amount where the
-- invoice dates are within 7 days of each other but the invoice_ids differ.
-- We flag the LATER of the two invoices (the suspected duplicate payment),
-- not the original.
--
-- WHY IT MATTERS TO AN AUDITOR
-- ----------------------------
-- Duplicate payments are the single most common cause of confirmed cash
-- leakage in accounts-payable audits.  A vendor submits the same invoice
-- twice (intentionally or by mistake), and if the AP team processes both,
-- the company pays twice for one service.  The entire amount of the later
-- payment is a direct, recoverable loss.  Auditors treat these as
-- "confirmed leakage" rather than mere "exposure", because recovery is
-- straightforward once identified.
--
-- KNOWN WEAKNESS
-- --------------
-- This rule matches on (vendor_id, amount_inr) only.  It will produce
-- false positives for vendors who legitimately raise invoices for the same
-- fixed fee multiple times in a short period (e.g., monthly retainers billed
-- twice in one week during a billing-cycle overlap).  It will also miss
-- duplicates where the amount differs slightly due to rounding or currency
-- conversion.  A real audit would also join on invoice reference numbers,
-- but those are not available in this synthetic dataset.
-- =============================================================================

SELECT
    b.invoice_id,
    'Duplicate: same vendor and amount as ' || a.invoice_id
        || ' (gap ' ||
        CAST(
            CAST(julianday(b.invoice_date) - julianday(a.invoice_date) AS INTEGER)
        AS TEXT)
        || ' days)' AS reason
FROM invoices a
JOIN invoices b
    ON  a.vendor_id    = b.vendor_id
    AND a.amount_inr   = b.amount_inr
    AND a.invoice_id  != b.invoice_id
    -- b is the LATER invoice
    AND b.invoice_date >= a.invoice_date
    AND julianday(b.invoice_date) - julianday(a.invoice_date) <= 7
-- Keep only the later invoice (b) by ensuring no earlier pair exists
-- that would make b the "original".  We achieve this by flagging b only
-- when a.invoice_date <= b.invoice_date, and then de-duplicating below
-- so each flagged invoice_id appears only once.
GROUP BY b.invoice_id
ORDER BY b.invoice_id
