-- =============================================================================
-- 05_outliers.sql
-- =============================================================================
--
-- WHAT IT DETECTS
-- ---------------
-- Invoices whose amount is more than 3 standard deviations above that
-- vendor's own mean invoice amount.  Only vendors with at least 10 invoices
-- are included (too few invoices makes mean/std unreliable).
--
-- SQLite does not have a built-in STDDEV function, so standard deviation is
-- computed manually using the identity:
--   std = sqrt( avg(x^2) - avg(x)^2 )
-- This is the population standard deviation (not sample), which is
-- appropriate here because we are using the full history of the vendor's
-- invoices, not a sample.
--
-- WHY IT MATTERS TO AN AUDITOR
-- ----------------------------
-- Every vendor has a "normal" billing range based on the services they
-- provide.  A single invoice that is dramatically higher than that vendor's
-- usual amounts could indicate:
--   - Overbilling: the vendor charged for work not done or inflated hours.
--   - Data entry error: a zero was accidentally added.
--   - Unauthorised purchase: someone placed a large order without approval.
-- Outlier invoices often represent the largest individual amounts in a
-- leakage portfolio, so catching even a few has high financial impact.
--
-- KNOWN WEAKNESS
-- --------------
-- The 3-sigma threshold assumes invoice amounts follow a roughly normal
-- distribution within each vendor, but lognormal distributions (which are
-- more realistic for invoice amounts) have heavier tails.  This means the
-- rule may flag legitimate invoices for vendors with high natural variance.
-- Also, if a vendor has submitted many fraudulent invoices over time, their
-- "normal" mean is inflated, which masks future outliers (the baseline is
-- poisoned).  A more robust approach would use median and MAD (median
-- absolute deviation) instead of mean and std.
-- =============================================================================

WITH vendor_stats AS (
    -- Compute per-vendor mean and population std dev (manual, no STDDEV func).
    -- avg_sq = average of squared amounts = E[X^2]
    -- sq_avg = square of average amount   = E[X]^2
    -- variance = E[X^2] - E[X]^2  (by the Var(X) = E[X^2] - E[X]^2 identity)
    SELECT
        vendor_id,
        COUNT(*)                              AS invoice_count,
        AVG(amount_inr)                       AS mean_amount,
        -- sqrt(max(..., 0)) guards against tiny floating-point negatives
        SQRT(
            MAX(AVG(amount_inr * amount_inr) - AVG(amount_inr) * AVG(amount_inr), 0)
        )                                     AS std_amount
    FROM invoices
    GROUP BY vendor_id
    HAVING COUNT(*) >= 10        -- need at least 10 invoices for a reliable baseline
)
SELECT
    i.invoice_id,
    'Outlier: amount Rs ' || CAST(CAST(i.amount_inr AS INTEGER) AS TEXT)
        || ' vs vendor mean Rs ' || CAST(CAST(vs.mean_amount AS INTEGER) AS TEXT)
        || ' (+'
        || CAST(
               CAST(
                   ROUND((i.amount_inr - vs.mean_amount) / NULLIF(vs.std_amount, 0), 1)
               AS REAL)
           AS TEXT)
        || ' std devs)' AS reason
FROM invoices i
JOIN vendor_stats vs ON i.vendor_id = vs.vendor_id
WHERE
    vs.std_amount > 0           -- exclude vendors with zero variance (all identical)
    AND i.amount_inr > vs.mean_amount + 3.0 * vs.std_amount
ORDER BY i.invoice_id
