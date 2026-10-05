-- =============================================================================
-- Redwood Retail: churn modelling on BigQuery.
--
-- WHY THIS WAS REWRITTEN
--
-- The previous script defined its label with a rule:
--
--     is_churned = days_since_last_purchase > 90
--                  OR login_frequency_monthly < 2
--                  OR cart_abandonment_count > 3
--                  OR return_rate_percent > 25
--                  OR complaints_count > 2
--                  OR sentiment_score < -0.3
--
-- and then trained on those same six columns. The model was therefore being
-- asked to rediscover a threshold it had already been handed, which is why it
-- scored so well and why the scores meant nothing: it was measuring the rule,
-- not churn. Several of the remaining features were near-proxies for the same
-- quantities (feedback_rating drives sentiment_score, abandoned_cart_value_90d
-- tracks cart_abandonment_count, support_tickets_count tracks complaints_count),
-- so removing the exact duplicates alone would not have fixed it.
--
-- Churn is now an observed outcome rather than a rule. Features are computed
-- from everything known up to a cutoff date; the label is whether the customer
-- actually purchased in the window *after* that cutoff. Nothing on the feature
-- side can see across the cutoff.
--
-- Train and score share one definition. `customer_features(as_of)` is a table
-- function called with the cutoff during training and with today's date during
-- inference, so the two cannot drift apart. That is the structural fix; a pair
-- of parallel views that merely look similar would rot within a release.
--
-- Source is the typed `retail_current` mirror rather than JSON in the CDC
-- ledger. The old feature view pulled every field through
-- COALESCE(SAFE_CAST(JSON_VALUE(...)), <default>), so a renamed or retyped
-- document field silently became a default value and the model quietly trained
-- on zeros. Reading typed columns turns that into an error.
--
-- Placeholders are substituted by run_bigquery_analysis.py.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Order facts.
--
-- One row per live order. Reads the CDC mirror, which already reflects deletes,
-- so cancelled-and-removed orders do not linger in the feature set. Rows with
-- no customer or no order date cannot contribute to a per-customer time series
-- and are dropped here rather than being silently coalesced to a default.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.order_facts` AS
SELECT
  customer_id,
  order_id,
  DATE(created_at)                        AS order_date,
  created_at,
  customer_name,
  customer_email,
  customer_segment,
  loyalty_tier,
  is_loyalty_member,
  account_age_days,
  order_status,
  payment_status,
  grand_total,
  profit_margin,
  -- Point-in-time behavioural readings carried on each order. They describe
  -- the customer as at the moment the order was placed, which is what makes
  -- them safe to use as features once windowed against a cutoff.
  login_frequency_monthly,
  avg_session_duration_minutes,
  app_engagement_score,
  cart_abandonment_count,
  abandoned_cart_value_90d,
  support_tickets_count,
  open_support_tickets_count,
  complaints_count,
  return_rate_percent,
  sentiment_score,
  has_active_complaint,
  feedback_rating
FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_ORDERS_TABLE}`
WHERE customer_id IS NOT NULL
  AND created_at IS NOT NULL
  -- A cancelled order is not evidence of engagement, and counting it as a
  -- purchase would mask exactly the disengagement we are trying to detect.
  AND COALESCE(order_status, '') != 'CANCELLED';


-- -----------------------------------------------------------------------------
-- 2. Feature table function.
--
-- Everything here is filtered to `order_date <= as_of`. That single predicate
-- is what makes the features honest: called with a past cutoff it reconstructs
-- what was knowable then, and called with CURRENT_DATE it produces the live
-- feature vector. Training and inference therefore run identical code.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE FUNCTION
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_features`(as_of DATE) AS (
  WITH
  visible_orders AS (
    SELECT *
    FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.order_facts`
    WHERE order_date <= as_of
  ),

  -- The most recent order per customer supplies the identity and behavioural
  -- readings. ROW_NUMBER rather than ANY_VALUE: the old view grouped by
  -- customer and wrapped every column in ANY_VALUE(), so each feature could
  -- come from a different order and the resulting row described no actual
  -- point in time. Ties are broken on order_id to keep runs reproducible.
  latest_order AS (
    SELECT * EXCEPT(rn)
    FROM (
      SELECT
        o.*,
        ROW_NUMBER() OVER (
          PARTITION BY customer_id
          ORDER BY created_at DESC, order_id DESC
        ) AS rn
      FROM visible_orders o
    )
    WHERE rn = 1
  ),

  aggregates AS (
    SELECT
      customer_id,
      COUNT(*)                                        AS orders_lifetime,
      SUM(grand_total)                                AS spend_lifetime,
      AVG(grand_total)                                AS avg_order_value,
      STDDEV_SAMP(grand_total)                        AS stddev_order_value,
      MIN(order_date)                                 AS first_order_date,
      MAX(order_date)                                 AS last_order_date,
      AVG(profit_margin)                              AS avg_profit_margin,

      COUNTIF(order_date > DATE_SUB(as_of, INTERVAL 90 DAY))   AS orders_90d,
      COUNTIF(order_date > DATE_SUB(as_of, INTERVAL 180 DAY))  AS orders_180d,
      COUNTIF(order_date > DATE_SUB(as_of, INTERVAL 365 DAY))  AS orders_365d,

      SUM(IF(order_date > DATE_SUB(as_of, INTERVAL 90 DAY), grand_total, 0))  AS spend_90d,
      SUM(IF(order_date > DATE_SUB(as_of, INTERVAL 365 DAY), grand_total, 0)) AS spend_365d,

      -- The 90 days before the trailing 90, used to express direction of
      -- travel. A customer spending steadily and one winding down can look
      -- identical on a single window.
      SUM(IF(order_date <= DATE_SUB(as_of, INTERVAL 90 DAY)
             AND order_date > DATE_SUB(as_of, INTERVAL 180 DAY),
             grand_total, 0))                         AS spend_prior_90d,
      COUNTIF(order_date <= DATE_SUB(as_of, INTERVAL 90 DAY)
              AND order_date > DATE_SUB(as_of, INTERVAL 180 DAY)) AS orders_prior_90d,

      AVG(IF(order_date > DATE_SUB(as_of, INTERVAL 180 DAY), sentiment_score, NULL))
                                                      AS avg_sentiment_180d,
      AVG(IF(order_date > DATE_SUB(as_of, INTERVAL 180 DAY), feedback_rating, NULL))
                                                      AS avg_rating_180d,
      COUNTIF(has_active_complaint)                   AS complaint_orders_lifetime
    FROM visible_orders
    GROUP BY customer_id
  )

  SELECT
    a.customer_id,

    -- Identity and segmentation
    l.customer_name,
    l.customer_email,
    l.customer_segment,
    l.loyalty_tier,
    COALESCE(l.is_loyalty_member, FALSE)              AS is_loyalty_member,
    l.account_age_days,

    -- Recency / frequency / monetary
    DATE_DIFF(as_of, a.last_order_date, DAY)          AS days_since_last_purchase,
    DATE_DIFF(as_of, a.first_order_date, DAY)         AS tenure_days,
    a.orders_lifetime,
    a.orders_90d,
    a.orders_180d,
    a.orders_365d,
    a.spend_lifetime,
    a.spend_90d,
    a.spend_365d,
    a.avg_order_value,
    COALESCE(a.stddev_order_value, 0.0)               AS stddev_order_value,
    a.avg_profit_margin,

    -- Average gap between orders so far. Undefined for a single order, which
    -- is left NULL rather than filled with a number that would imply a cadence
    -- we have not observed.
    SAFE_DIVIDE(
      DATE_DIFF(a.last_order_date, a.first_order_date, DAY),
      NULLIF(a.orders_lifetime - 1, 0)
    )                                                 AS avg_interpurchase_days,

    -- Silence measured in units of that customer's own rhythm. Thirty days
    -- quiet means nothing for a quarterly buyer and a great deal for a weekly
    -- one, so the absolute recency above cannot separate them.
    SAFE_DIVIDE(
      DATE_DIFF(as_of, a.last_order_date, DAY),
      NULLIF(SAFE_DIVIDE(
        DATE_DIFF(a.last_order_date, a.first_order_date, DAY),
        NULLIF(a.orders_lifetime - 1, 0)
      ), 0)
    )                                                 AS recency_vs_cadence,

    -- Direction of travel. Below 1.0 means the customer is winding down.
    SAFE_DIVIDE(a.spend_90d, NULLIF(a.spend_prior_90d, 0))   AS spend_trend_ratio,
    SAFE_DIVIDE(a.orders_90d, NULLIF(a.orders_prior_90d, 0)) AS order_trend_ratio,

    -- Engagement and support, as last observed at or before the cutoff.
    l.login_frequency_monthly,
    l.avg_session_duration_minutes,
    l.app_engagement_score,
    l.cart_abandonment_count,
    l.abandoned_cart_value_90d,
    l.support_tickets_count,
    l.open_support_tickets_count,
    l.complaints_count,
    l.return_rate_percent,
    a.avg_sentiment_180d                              AS sentiment_score,
    a.avg_rating_180d,
    a.complaint_orders_lifetime,

    as_of                                             AS feature_as_of_date
  FROM aggregates a
  JOIN latest_order l USING (customer_id)
);


-- -----------------------------------------------------------------------------
-- 3. Label table function.
--
-- Churn is "bought nothing in the horizon following the cutoff". This is the
-- only place the future is consulted, and the feature function above never
-- reaches past `as_of`, so the two windows cannot overlap.
--
-- Only customers with a purchase history at the cutoff get a label; a customer
-- whose first order arrives after the cutoff cannot meaningfully be said to
-- have churned at it.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE FUNCTION
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_labels`(
    as_of DATE, horizon_days INT64
  ) AS (
  SELECT
    customer_id,
    IF(
      COUNTIF(
        order_date > as_of
        AND order_date <= DATE_ADD(as_of, INTERVAL horizon_days DAY)
      ) > 0,
      0, 1
    )                                                 AS is_churned,
    COUNTIF(
      order_date > as_of
      AND order_date <= DATE_ADD(as_of, INTERVAL horizon_days DAY)
    )                                                 AS orders_in_horizon
  FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.order_facts`
  GROUP BY customer_id
  HAVING COUNTIF(order_date <= as_of) > 0
);


-- -----------------------------------------------------------------------------
-- 4. Training set.
--
-- The cutoff sits one horizon back from today so the outcome window has fully
-- elapsed; scoring a horizon that has not finished yet would label
-- still-active customers as churned purely because the clock has not run out.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_training_data` AS
WITH cutoff AS (
  SELECT DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AS as_of
)
SELECT
  f.*,
  l.is_churned,
  l.orders_in_horizon
FROM cutoff c
CROSS JOIN `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_features`(
  (SELECT as_of FROM cutoff)
) f
JOIN `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_labels`(
  (SELECT as_of FROM cutoff), 90
) l
USING (customer_id);


-- -----------------------------------------------------------------------------
-- 5. Live feature view, kept under the historical name so existing readers and
--    the loyalty agent do not have to change.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}` AS
SELECT *
FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_features`(CURRENT_DATE());


-- -----------------------------------------------------------------------------
-- 6. The model.
--
-- Logistic regression, for a demo where an explainable coefficient per feature
-- is worth more than a couple of points of AUC. Identity columns are excluded:
-- customer_id, name and email are unique per row and would let a tree-based
-- learner memorise, and carry no signal for a regression either.
--
-- auto_class_weights is on because the classes are unbalanced (roughly 29%
-- churn); without it the model can score well by predicting "retained" for
-- everyone, which is precisely the customer it exists to catch.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`
OPTIONS (
  model_type              = 'LOGISTIC_REG',
  input_label_cols        = ['is_churned'],
  auto_class_weights      = TRUE,
  data_split_method       = 'RANDOM',
  data_split_eval_fraction = 0.2,
  l2_reg                  = 0.1,
  max_iterations          = 50,
  early_stop              = TRUE,
  enable_global_explain   = TRUE
) AS
SELECT * EXCEPT (
  customer_id,
  customer_name,
  customer_email,
  feature_as_of_date,
  -- Dropped: this counts the very purchases the label is defined on.
  orders_in_horizon
)
FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_training_data`;


-- -----------------------------------------------------------------------------
-- 7. Evaluation, persisted so a regression is visible rather than buried in a
--    job history. Worth reading after every run: a near-perfect score on a
--    problem like this is evidence of leakage, not of a good model.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_model_evaluation` AS
SELECT
  CURRENT_TIMESTAMP() AS evaluated_at,
  *
FROM ML.EVALUATE(
  MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`
);


-- -----------------------------------------------------------------------------
-- 8. Score every customer and merge into the serving table.
--
-- MERGE rather than CREATE OR REPLACE TABLE. The previous script replaced the
-- table outright, which dropped the schema, partitioning and clustering that
-- Terraform had declared; the drift was hidden behind an ignore_changes block
-- rather than fixed. A merge leaves the table definition alone and keeps
-- exactly one current row per customer, which is what the agent reads.
-- -----------------------------------------------------------------------------
MERGE `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_risk` AS target
USING (
  SELECT
    customer_id,
    customer_name,
    customer_email,
    customer_segment,
    loyalty_tier,
    predicted_is_churned,
    churn_probability,
    CASE
      WHEN churn_probability >= 0.80 THEN 'CRITICAL'
      WHEN churn_probability >= 0.60 THEN 'HIGH'
      WHEN churn_probability >= 0.40 THEN 'MODERATE'
      ELSE 'LOW'
    END AS churn_risk_tier,
    total_spend_90d,
    days_since_last_purchase,
    cart_abandonment_count,
    support_tickets_count,
    sentiment_score,
    -- The intervention is chosen from value as well as risk: a high-risk
    -- customer who spends little does not justify the same concession as one
    -- who spends a lot, and the agent uses this as a starting recommendation.
    CASE
      WHEN churn_probability >= 0.80 AND total_spend_90d >= 5000
        THEN 'EXECUTIVE_OUTREACH_PLUS_TIER_UPGRADE'
      WHEN churn_probability >= 0.80
        THEN 'HIGH_VALUE_RETENTION_OFFER'
      WHEN churn_probability >= 0.60 AND cart_abandonment_count >= 3
        THEN 'CART_RECOVERY_INCENTIVE'
      WHEN churn_probability >= 0.60
        THEN 'TARGETED_LOYALTY_DISCOUNT'
      WHEN churn_probability >= 0.40
        THEN 'ENGAGEMENT_CAMPAIGN'
      ELSE 'MONITOR_ONLY'
    END AS automated_retention_action,
    CURRENT_TIMESTAMP() AS calculation_timestamp
  FROM (
    SELECT
      f.customer_id,
      f.customer_name,
      f.customer_email,
      f.customer_segment,
      f.loyalty_tier,
      f.spend_90d                       AS total_spend_90d,
      f.days_since_last_purchase,
      f.cart_abandonment_count,
      f.support_tickets_count,
      f.sentiment_score,
      p.predicted_is_churned,
      -- ML.PREDICT returns one probability per class; pick the positive one
      -- explicitly rather than relying on array ordering.
      (
        SELECT prob
        FROM UNNEST(p.predicted_is_churned_probs)
        WHERE label = 1
      )                                 AS churn_probability
    FROM ML.PREDICT(
      MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`,
      TABLE `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`
    ) AS p
    JOIN `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}` AS f
      USING (customer_id)
  )
) AS source
ON target.customer_id = source.customer_id
WHEN MATCHED THEN UPDATE SET
  customer_name              = source.customer_name,
  customer_email             = source.customer_email,
  customer_segment           = source.customer_segment,
  loyalty_tier               = source.loyalty_tier,
  predicted_is_churned       = source.predicted_is_churned,
  churn_probability          = source.churn_probability,
  churn_risk_tier            = source.churn_risk_tier,
  total_spend_90d            = source.total_spend_90d,
  days_since_last_purchase   = source.days_since_last_purchase,
  cart_abandonment_count     = source.cart_abandonment_count,
  support_tickets_count      = source.support_tickets_count,
  sentiment_score            = source.sentiment_score,
  automated_retention_action = source.automated_retention_action,
  calculation_timestamp      = source.calculation_timestamp
WHEN NOT MATCHED THEN INSERT (
  customer_id, customer_name, customer_email, customer_segment, loyalty_tier,
  predicted_is_churned, churn_probability, churn_risk_tier,
  total_spend_90d, days_since_last_purchase, cart_abandonment_count,
  support_tickets_count, sentiment_score, automated_retention_action,
  calculation_timestamp
) VALUES (
  source.customer_id, source.customer_name, source.customer_email,
  source.customer_segment, source.loyalty_tier,
  source.predicted_is_churned, source.churn_probability, source.churn_risk_tier,
  source.total_spend_90d, source.days_since_last_purchase,
  source.cart_abandonment_count, source.support_tickets_count,
  source.sentiment_score, source.automated_retention_action,
  source.calculation_timestamp
)
-- Drop customers the source no longer knows about. Without this the table only
-- ever grows: re-seeding the demo produces a fresh set of customer ids, the old
-- ones stop matching, and their scores sit there indefinitely. A run against a
-- 402-customer dataset left 650 rows behind, 250 of them scored from data that
-- had already been deleted, and the agent has no way to tell those apart from
-- live ones.
WHEN NOT MATCHED BY SOURCE THEN DELETE;

