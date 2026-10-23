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
-- Same-day tie-breaking: when both invoices share the same invoice_date
-- (gap = 0 days), there is no date-based way to tell which came first.
-- We break the tie by flagging the invoice with the lexicographically
-- GREATER invoice_id (i.e. a.invoice_id < b.invoice_id).  This is a known
-- weakness: the invoice_id order is an artifact of the data pipeline, not
-- a true temporal signal.  In a real system, use invoice receipt timestamp
-- or the vendor's own invoice reference number to break ties.
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
-- This rule matches on (vendor_id, amount_inr) only -- no invoice reference
-- number.  It will produce false positives for vendors who legitimately
-- raise invoices for the same fixed fee multiple times in a short period
-- (e.g. retainer vendors with a recurring monthly charge, or a vendor who
-- replaces a rejected invoice with an identical one).  It will also miss
-- duplicates where the amount differs slightly due to rounding, currency
-- conversion, or a partial credit note.
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
    ON  a.vendor_id  = b.vendor_id
    AND a.amount_inr = b.amount_inr
    AND (
        -- Case 1: b is strictly later than a (positive gap, up to 7 days)
        (    b.invoice_date >  a.invoice_date
         AND julianday(b.invoice_date) - julianday(a.invoice_date) <= 7
        )
        OR
        -- Case 2: same invoice_date -- break tie by invoice_id so each pair
        -- is visited exactly once and only the 'higher' id is flagged.
        -- KNOWN WEAKNESS: invoice_id order is pipeline order, not arrival order.
        (    b.invoice_date = a.invoice_date
         AND a.invoice_id   < b.invoice_id
        )
    )
ORDER BY b.invoice_id
