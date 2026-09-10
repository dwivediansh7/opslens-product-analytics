-- ============================================================
-- OpsLens :: product_health.sql
-- Establishes a clean base view, then answers:
--   Q1. What is overall product health?
--   Q2. Which product area performs worst?
--   Q3. Does the ranking change depending on the metric we use?
-- ============================================================


-- ------------------------------------------------------------
-- BASE VIEW
-- Every downstream query reads from this, so cleaning rules are
-- defined once instead of being copy-pasted into ten queries.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS clean_tickets;

CREATE VIEW clean_tickets AS
SELECT
    ticket_id,
    user_id,
    created_at,
    substr(created_at, 1, 7)                        AS month,
    product_area,
    issue_type,
    priority,
    status,
    customer_text,
    -- blank segments become an explicit bucket, never silently dropped
    CASE WHEN TRIM(COALESCE(user_segment, '')) = ''
         THEN 'Unknown'
         ELSE TRIM(user_segment)
    END                                             AS user_segment,
    UPPER(TRIM(region))                             AS region,
    channel,
    repeat_contact,
    sla_breached,
    -- negative durations are clock/timezone bugs, not fast resolutions
    CASE WHEN resolution_hours < 0 THEN NULL
         ELSE resolution_hours
    END                                             AS resolution_hours,
    -- valid CSAT is 1-5; 0 means "no valid response"
    CASE WHEN csat BETWEEN 1 AND 5 THEN csat
         ELSE NULL
    END                                             AS csat
FROM tickets
GROUP BY ticket_id;   -- collapses the duplicated export rows


-- ============================================================
-- Q1. BUSINESS QUESTION
-- "What does product health look like overall right now?"
-- We need a single baseline row. Without a baseline, every later
-- number is meaningless -- 31% is only bad relative to something.
-- ============================================================
SELECT
    COUNT(*)                                                    AS tickets,
    COUNT(DISTINCT user_id)                                     AS users,
    ROUND(AVG(CASE WHEN status = 'resolved' THEN 1.0 ELSE 0 END), 4) AS resolution_rate,
    ROUND(AVG(repeat_contact * 1.0), 4)                         AS repeat_contact_rate,
    ROUND(AVG(sla_breached * 1.0), 4)                           AS sla_breach_rate,
    ROUND(AVG(resolution_hours), 2)                             AS mean_resolution_hours,
    ROUND(AVG(csat), 3)                                         AS avg_csat
FROM clean_tickets;


-- ============================================================
-- Q2. BUSINESS QUESTION
-- "Which product area is performing worst?"
--
-- Deliberately shown three ways at once: absolute volume,
-- absolute breach count, and rates. If they disagree, the
-- disagreement IS the finding.
-- ============================================================
SELECT
    product_area,
    COUNT(*)                                        AS tickets,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_volume,
    SUM(sla_breached)                               AS sla_breaches_absolute,
    ROUND(AVG(sla_breached * 1.0), 4)               AS sla_breach_rate,
    ROUND(AVG(repeat_contact * 1.0), 4)             AS repeat_contact_rate,
    ROUND(AVG(resolution_hours), 2)                 AS mean_resolution_hours,
    ROUND(AVG(csat), 3)                             AS avg_csat
FROM clean_tickets
GROUP BY product_area
ORDER BY tickets DESC;


-- ============================================================
-- Q3. BUSINESS QUESTION
-- "If I rank product areas by each metric separately, do I get
--  the same answer?"
--
-- This is the prioritisation question. A team that ranks by
-- volume and a team that ranks by rate will fund different work.
-- ============================================================
WITH area_metrics AS (
    SELECT
        product_area,
        COUNT(*)                             AS tickets,
        AVG(repeat_contact * 1.0)            AS repeat_rate,
        AVG(sla_breached * 1.0)              AS breach_rate,
        AVG(resolution_hours)                AS mean_hours,
        AVG(csat)                            AS avg_csat
    FROM clean_tickets
    GROUP BY product_area
)
SELECT
    product_area,
    tickets,
    RANK() OVER (ORDER BY tickets     DESC) AS rank_by_volume,
    RANK() OVER (ORDER BY repeat_rate DESC) AS rank_by_repeat,
    RANK() OVER (ORDER BY breach_rate DESC) AS rank_by_breach,
    RANK() OVER (ORDER BY mean_hours  DESC) AS rank_by_slowness,
    RANK() OVER (ORDER BY avg_csat    ASC)  AS rank_by_worst_csat
FROM area_metrics
ORDER BY rank_by_repeat;
