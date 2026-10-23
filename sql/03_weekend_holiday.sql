-- =============================================================================
-- 03_weekend_holiday.sql
-- =============================================================================
--
-- WHAT IT DETECTS
-- ---------------
-- Invoices whose payment_date falls on a Saturday, a Sunday, or a date
-- that appears in the 'holidays' table (Indian national holidays).
--
-- WHY IT MATTERS TO AN AUDITOR
-- ----------------------------
-- Legitimate payments are almost always processed on working days when
-- finance staff, approvers, and banking systems are all active.  A payment
-- on a weekend or public holiday is unusual because:
--   1. Most ERP/banking systems require human authorisation that is not
--      available outside office hours.
--   2. Fraudulent payments are sometimes timed for weekends precisely to
--      avoid scrutiny — colleagues and managers are absent and less likely
--      to notice the transaction immediately.
-- These are classified as "exposure at risk" rather than confirmed leakage:
-- some legitimate payments do fall on non-working days (urgent disbursements,
-- automated standing orders), so an auditor must investigate each case.
--
-- KNOWN WEAKNESS
-- --------------
-- The holiday list is short and covers only central-government holidays.
-- State-specific holidays and bank holidays (which vary by state) are not
-- included, so the rule will miss some true positives and also flag dates
-- that are normal working days for the company's specific location.
-- About 2% of normal payments also naturally land on weekends due to the
-- 15-45 day payment lag, producing unavoidable false positives.
-- =============================================================================

SELECT
    i.invoice_id,
    'Weekend/holiday payment: payment_date ' || i.payment_date
        || ' is a '
        || CASE
               WHEN CAST(strftime('%w', i.payment_date) AS INTEGER) = 0 THEN 'Sunday'
               WHEN CAST(strftime('%w', i.payment_date) AS INTEGER) = 6 THEN 'Saturday'
               ELSE 'public holiday (' || h.holiday_name || ')'
           END AS reason
FROM invoices i
LEFT JOIN holidays h
    ON h.date = i.payment_date
WHERE
    -- Saturday: strftime('%w') = '6'
    CAST(strftime('%w', i.payment_date) AS INTEGER) = 6
    OR
    -- Sunday: strftime('%w') = '0'
    CAST(strftime('%w', i.payment_date) AS INTEGER) = 0
    OR
    -- Public holiday
    h.date IS NOT NULL
ORDER BY i.invoice_id
