-- ============================================================
-- OpsLens :: problem_analysis.sql
--
-- Block 1 told us WHICH product area is failing.
-- This file narrows it to WHAT, WHO and WHEN:
--   Q4. Which issue types inside that area drive the repeat contacts?
--   Q5. Which customer segment absorbs the friction?
--   Q6. When did it start -- and did anything else move at the same time?
--   Q7. Can we derive repeat_contact from raw data instead of trusting the column?
--
-- Assumes clean_tickets already exists (created in product_health.sql).
-- ============================================================


-- ============================================================
-- Q4. BUSINESS QUESTION
-- "A product area is too big to fix. Which specific issue type
--  inside it is actually generating the repeat contacts?"
--
-- The baseline is included on every row so each rate can be read
-- as a lift, not as a bare number. 31% means nothing on its own.
-- ============================================================
WITH baseline AS (
    SELECT AVG(repeat_contact * 1.0) AS overall_repeat_rate
    FROM clean_tickets
)
SELECT
    t.issue_type,
    COUNT(*)                                            AS tickets,
    ROUND(AVG(t.repeat_contact * 1.0), 4)               AS repeat_rate,
    ROUND(AVG(t.repeat_contact * 1.0) / b.overall_repeat_rate, 2) AS lift_vs_baseline,
    ROUND(AVG(t.resolution_hours), 1)                   AS mean_hours,
    ROUND(AVG(t.csat), 2)                               AS avg_csat,
    -- excess contacts = the tickets that would not exist at baseline
    ROUND(COUNT(*) * (AVG(t.repeat_contact * 1.0) - b.overall_repeat_rate)) AS excess_contacts
FROM clean_tickets t
CROSS JOIN baseline b
WHERE t.product_area = 'Refunds'
GROUP BY t.issue_type, b.overall_repeat_rate
ORDER BY excess_contacts DESC;


-- ============================================================
-- Q5. BUSINESS QUESTION
-- "Is this hurting everyone equally, or is one customer segment
--  taking the hit?"
--
-- Each segment is compared against ITS OWN behaviour elsewhere in
-- the product. Otherwise we would just rediscover that some
-- segments complain more in general.
-- ============================================================
WITH segment_rates AS (
    SELECT
        user_segment,
        AVG(CASE WHEN product_area = 'Refunds' THEN repeat_contact * 1.0 END) AS refunds_repeat_rate,
        AVG(CASE WHEN product_area <> 'Refunds' THEN repeat_contact * 1.0 END) AS other_repeat_rate,
        SUM(CASE WHEN product_area = 'Refunds' THEN 1 ELSE 0 END)             AS refunds_tickets
    FROM clean_tickets
    GROUP BY user_segment
)
SELECT
    user_segment,
    refunds_tickets,
    ROUND(refunds_repeat_rate, 4)                        AS refunds_repeat_rate,
    ROUND(other_repeat_rate, 4)                          AS elsewhere_repeat_rate,
    ROUND(refunds_repeat_rate - other_repeat_rate, 4)    AS friction_gap
FROM segment_rates
WHERE refunds_tickets > 100          -- ignore segments too small to trust
ORDER BY friction_gap DESC;


-- ============================================================
-- Q6. BUSINESS QUESTION
-- "When did this start, and is it specific to Refunds -- or is
--  the whole product degrading?"
--
-- The second column is the control. If every area worsened in the
-- same month, this is not a Refunds problem, it is a company
-- problem, and the recommendation would be completely different.
-- ============================================================
SELECT
    month,
    SUM(CASE WHEN product_area = 'Refunds' THEN 1 ELSE 0 END)   AS refunds_tickets,
    ROUND(AVG(CASE WHEN product_area = 'Refunds'
                   THEN repeat_contact * 1.0 END), 4)           AS refunds_repeat_rate,
    ROUND(AVG(CASE WHEN product_area <> 'Refunds'
                   THEN repeat_contact * 1.0 END), 4)           AS control_repeat_rate,
    ROUND(AVG(CASE WHEN product_area = 'Refunds'
                   THEN resolution_hours END), 1)               AS refunds_mean_hours
FROM clean_tickets
GROUP BY month
ORDER BY month;


-- ============================================================
-- Q7. BUSINESS QUESTION
-- "We are betting the whole analysis on the repeat_contact column.
--  Can we reproduce it from raw event data?"
--
-- Definition: a ticket is a repeat contact if the SAME user raised
-- a ticket in the SAME product area within the previous 7 days.
-- LAG() looks back one row inside each user+area group.
--
-- Never take a pre-computed flag on trust. Someone defined it, and
-- you need to know what they decided.
-- ============================================================
WITH ordered AS (
    SELECT
        ticket_id,
        user_id,
        product_area,
        created_at,
        repeat_contact                                  AS flag_in_data,
        LAG(created_at) OVER (
            PARTITION BY user_id, product_area
            ORDER BY created_at
        )                                               AS previous_contact_at
    FROM clean_tickets
),
derived AS (
    SELECT
        ticket_id,
        product_area,
        flag_in_data,
        CASE
            WHEN previous_contact_at IS NOT NULL
             AND julianday(REPLACE(created_at, 'T', ' '))
               - julianday(REPLACE(previous_contact_at, 'T', ' ')) <= 7
            THEN 1 ELSE 0
        END                                             AS flag_derived
    FROM ordered
)
SELECT
    COUNT(*)                                                        AS tickets,
    SUM(flag_in_data)                                               AS flagged_in_data,
    SUM(flag_derived)                                               AS flagged_by_our_rule,
    ROUND(100.0 * SUM(CASE WHEN flag_in_data = flag_derived
                           THEN 1 ELSE 0 END) / COUNT(*), 2)        AS pct_agreement
FROM derived;
