-- =============================================================================
-- 04_round_figure.sql
-- =============================================================================
--
-- WHAT IT DETECTS
-- ---------------
-- Invoices whose amount is an exact multiple of 10,000 and is >= 20,000,
-- BUT only for vendors whose overall rate of round-figure invoices is below
-- 15%.  Vendors with a high natural rate of round figures (retainer vendors,
-- rent, maintenance contracts) are excluded from flagging.
--
-- WHY IT MATTERS TO AN AUDITOR
-- ----------------------------
-- Real-world invoices almost never end in a round number: they include
-- taxes, line-item pricing, shipping charges, and other specifics that
-- produce irregular amounts.  A perfectly round amount (e.g., Rs 40,000
-- exactly) can indicate that the invoice was created retrospectively to
-- justify a payment that was already decided, or that the amount was
-- estimated rather than calculated.  This is a soft red flag that, combined
-- with other signals, raises the overall risk score of an invoice.
--
-- KNOWN WEAKNESS
-- --------------
-- The 15% threshold for "naturally round" vendors is a heuristic.  Some
-- genuine expense categories (e.g., software licences with fixed annual
-- fees, advances) always produce round amounts regardless of vendor type.
-- The rule will still produce false positives for new vendors who have
-- issued only a few invoices and happened to submit round amounts.  The
-- Rs 20,000 floor reduces noise but also means small round-figure invoices
-- (e.g., Rs 10,000) are not flagged.
-- =============================================================================

WITH vendor_stats AS (
    -- For each vendor, calculate the total invoice count and the count of
    -- round-figure invoices (multiples of 10,000 and >= 20,000).
    SELECT
        vendor_id,
        COUNT(*)                                          AS total_invoices,
        SUM(
            CASE
                WHEN amount_inr >= 20000
                     AND (CAST(amount_inr AS INTEGER) % 10000) = 0
                     AND amount_inr = CAST(amount_inr AS INTEGER)  -- must be whole number
                THEN 1
                ELSE 0
            END
        )                                                 AS round_count,
        -- Round-figure rate for this vendor (as a fraction)
        CAST(
            SUM(
                CASE
                    WHEN amount_inr >= 20000
                         AND (CAST(amount_inr AS INTEGER) % 10000) = 0
                         AND amount_inr = CAST(amount_inr AS INTEGER)
                    THEN 1 ELSE 0
                END
            ) AS REAL
        ) / COUNT(*)                                      AS round_rate
    FROM invoices
    GROUP BY vendor_id
)
SELECT
    i.invoice_id,
    'Round figure: amount Rs ' || CAST(CAST(i.amount_inr AS INTEGER) AS TEXT)
        || ' for vendor ' || i.vendor_id
        || ' (vendor round rate '
        || CAST(CAST(vs.round_rate * 100 AS INTEGER) AS TEXT)
        || '%, below 15% threshold)' AS reason
FROM invoices i
JOIN vendor_stats vs ON i.vendor_id = vs.vendor_id
WHERE
    -- Invoice amount is a round multiple of 10,000 and at least Rs 20,000
    i.amount_inr >= 20000
    AND (CAST(i.amount_inr AS INTEGER) % 10000) = 0
    AND i.amount_inr = CAST(i.amount_inr AS INTEGER)
    -- Vendor does NOT naturally issue round figures (rate < 15%)
    AND vs.round_rate < 0.15
ORDER BY i.invoice_id
