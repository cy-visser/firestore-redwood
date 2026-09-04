-- ==============================================================================
-- BigQuery Analytics Workbook: Redwood Retail Customer Churn Prediction (BQML)
-- Dataset: `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}`
-- Replicated in real-time from Firestore Enterprise via Cloud Dataflow CDC
--
-- Note: Placeholders (${GCP_PROJECT_ID}, ${BIGQUERY_DATASET}, ${BIGQUERY_CDC_TABLE},
-- ${BIGQUERY_HISTORICAL_VIEW}, ${BIGQUERY_CHURN_MODEL}) are populated dynamically
-- from your .env file via `run_bigquery_analysis.py`.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Feature Engineering View: `${BIGQUERY_HISTORICAL_VIEW}`
-- Extracts and aggregates 4 data pillars across all customer touchpoints:
--   1) Transactional Data (Spend, AOV, Frequency, Recency)
--   2) Engagement & Activity (Logins, Duration, App Score, Abandonment)
--   3) Customer Support / Satisfaction (Tickets, Complaints, Returns, Sentiment)
--   4) Demographics & Account State (Loyalty Tier, Account Age, Location)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE VIEW `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}` AS
WITH customer_raw_features AS (
  SELECT
    COALESCE(customer_id, JSON_VALUE(document_data, '$.customerId')) AS customer_id,
    ANY_VALUE(COALESCE(customer_name, JSON_VALUE(document_data, '$.customerName'))) AS customer_name,
    ANY_VALUE(COALESCE(customer_email, JSON_VALUE(document_data, '$.customerEmail'))) AS customer_email,
    ANY_VALUE(COALESCE(customer_segment, JSON_VALUE(document_data, '$.customerSegment'))) AS customer_segment,

    -- Demographics & Account State
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.accountState.isLoyaltyMember') AS INT64),
      0
    )) AS is_loyalty_member,
    ANY_VALUE(COALESCE(
      JSON_VALUE(document_data, '$.accountState.loyaltyTier'),
      'NONE'
    )) AS loyalty_tier,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.accountState.accountAgeDays') AS INT64),
      180
    )) AS account_age_days,
    ANY_VALUE(JSON_VALUE(document_data, '$.shippingAddress.city')) AS location_city,
    ANY_VALUE(JSON_VALUE(document_data, '$.shippingAddress.countryCode')) AS location_country,

    -- Transactional Data
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.totalSpend90d') AS FLOAT64),
      grand_total
    )) AS total_spend_90d,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.lifetimeSpend') AS FLOAT64),
      grand_total
    )) AS lifetime_spend,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.avgOrderValue') AS FLOAT64),
      grand_total
    )) AS avg_order_value,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.purchaseFrequencyMonthly') AS FLOAT64),
      1.0
    )) AS purchase_frequency_monthly,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.daysSinceLastPurchase') AS INT64),
      15
    )) AS days_since_last_purchase,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.transactionalMetrics.ordersCountLast12m') AS INT64),
      1
    )) AS orders_count_last_12m,

    -- Engagement & Activity
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.loginFrequencyMonthly') AS INT64),
      10
    )) AS login_frequency_monthly,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.avgSessionDurationMinutes') AS FLOAT64),
      8.5
    )) AS avg_session_duration_minutes,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.appEngagementScore') AS FLOAT64),
      0.50
    )) AS app_engagement_score,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.appSessionsLast30d') AS INT64),
      10
    )) AS app_sessions_last_30d,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.cartAbandonmentCount') AS INT64),
      0
    )) AS cart_abandonment_count,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.engagement.abandonedCartValue90d') AS FLOAT64),
      0.0
    )) AS abandoned_cart_value_90d,

    -- Customer Support & Satisfaction
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.supportTicketsCount') AS INT64),
      0
    )) AS support_tickets_count,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.openSupportTicketsCount') AS INT64),
      0
    )) AS open_support_tickets_count,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.complaintsCount') AS INT64),
      0
    )) AS complaints_count,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.returnFrequency') AS INT64),
      0
    )) AS return_frequency,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.returnRatePercent') AS FLOAT64),
      0.0
    )) AS return_rate_percent,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.supportMetrics.sentimentScore') AS FLOAT64),
      SAFE_CAST(JSON_VALUE(document_data, '$.customerFeedback.sentimentScore') AS FLOAT64),
      0.50
    )) AS sentiment_score,
    ANY_VALUE(COALESCE(
      SAFE_CAST(JSON_VALUE(document_data, '$.customerFeedback.rating') AS INT64),
      4
    )) AS feedback_rating

  FROM
    `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CDC_TABLE}`
  WHERE
    COALESCE(customer_id, JSON_VALUE(document_data, '$.customerId')) IS NOT NULL
  GROUP BY
    customer_id
)
SELECT
  *,
  -- Business Rule: Calculate ground-truth is_churned label directly in BigQuery
  -- A customer is classified as churned if:
  --   1) Inactivity >= 60 days AND exhibiting severe engagement drop (<= 3 logins/mo OR >= 3 cart abandonments OR >= 15% return rate)
  --   2) Or high complaint volume with negative sentiment (>= 2 complaints AND sentiment_score < -0.30)
  CASE
    WHEN days_since_last_purchase >= 60 
      AND (login_frequency_monthly <= 3 OR cart_abandonment_count >= 3 OR return_rate_percent >= 15.0)
      THEN 1
    WHEN complaints_count >= 2 AND sentiment_score < -0.30
      THEN 1
    ELSE 0
  END AS is_churned
FROM
  customer_raw_features;


-- ------------------------------------------------------------------------------
-- 2. Train BigQuery ML Logistic Regression Model (`${BIGQUERY_CHURN_MODEL}`)
-- Uses L2 regularization, class balancing, and automated feature preprocessing.
-- ------------------------------------------------------------------------------
CREATE OR REPLACE MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`
OPTIONS(
  model_type = 'logistic_reg',
  input_label_cols = ['is_churned'],
  auto_class_weights = TRUE,
  data_split_method = 'AUTO_SPLIT',
  l2_reg = 0.1,
  max_iterations = 25,
  enable_global_explain = TRUE
) AS
SELECT
  -- Transactional Features
  total_spend_90d,
  days_since_last_purchase,
  avg_order_value,
  purchase_frequency_monthly,
  orders_count_last_12m,
  -- Engagement & Activity Features
  login_frequency_monthly,
  avg_session_duration_minutes,
  app_engagement_score,
  cart_abandonment_count,
  abandoned_cart_value_90d,
  -- Customer Support & Satisfaction Features
  support_tickets_count,
  open_support_tickets_count,
  complaints_count,
  return_rate_percent,
  sentiment_score,
  feedback_rating,
  -- Demographics & Account State Features
  is_loyalty_member,
  loyalty_tier,
  account_age_days,
  customer_segment,
  location_country,
  -- Target Label
  is_churned
FROM
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`;


-- ------------------------------------------------------------------------------
-- 3. Model Evaluation: Comprehensive Performance Metrics
-- Returns Precision, Recall, Accuracy, F1-score, Log Loss, and ROC AUC
-- ------------------------------------------------------------------------------
SELECT
  *
FROM
  ML.EVALUATE(
    MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`,
    (
      SELECT * FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`
    )
  );

-- Confusion Matrix
SELECT
  *
FROM
  ML.CONFUSION_MATRIX(
    MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`,
    (
      SELECT * FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`
    )
  );

-- ROC Curve and Threshold Tradeoffs
SELECT
  *
FROM
  ML.ROC_CURVE(
    MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`,
    (
      SELECT * FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`
    )
  );


-- ------------------------------------------------------------------------------
-- 4. Model Explainability: Global Feature Importance & Weights
-- Identifies top factors influencing customer churn predictions
-- ------------------------------------------------------------------------------
SELECT
  feature,
  attribution
FROM
  ML.GLOBAL_EXPLAIN(MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`)
ORDER BY
  attribution DESC;

-- Raw Logistic Regression Feature Weights
SELECT
  processed_input,
  weight,
  category_weights
FROM
  ML.WEIGHTS(MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`)
ORDER BY
  ABS(weight) DESC;


-- ------------------------------------------------------------------------------
-- 5. Batch Churn Inference Table Materialization (`customer_churn_risk`)
-- Materializes daily baseline predictions to eliminate OLTP login latency (SDD Section 1.2)
-- ------------------------------------------------------------------------------
CREATE OR REPLACE TABLE `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_risk`
PARTITION BY DATE(calculation_timestamp)
CLUSTER BY customer_id, churn_risk_tier
AS
WITH customer_predictions AS (
  SELECT
    customer_id,
    customer_name,
    customer_email,
    customer_segment,
    loyalty_tier,
    predicted_is_churned,
    prob.prob AS churn_probability,
    total_spend_90d,
    days_since_last_purchase,
    cart_abandonment_count,
    support_tickets_count,
    sentiment_score
  FROM
    ML.PREDICT(
      MODEL `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_CHURN_MODEL}`,
      (SELECT * FROM `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.${BIGQUERY_HISTORICAL_VIEW}`)
    ),
    UNNEST(predicted_is_churned_probs) AS prob
  WHERE
    prob.label = 1
)
SELECT
  customer_id,
  customer_name,
  customer_email,
  customer_segment,
  loyalty_tier,
  predicted_is_churned,
  ROUND(churn_probability, 4) AS churn_probability,
  CASE
    WHEN churn_probability >= 0.75 THEN 'CRITICAL'
    WHEN churn_probability >= 0.50 THEN 'HIGH'
    WHEN churn_probability >= 0.25 THEN 'MODERATE'
    ELSE 'LOW'
  END AS churn_risk_tier,
  total_spend_90d,
  days_since_last_purchase,
  cart_abandonment_count,
  support_tickets_count,
  sentiment_score,
  CASE
    WHEN churn_probability >= 0.75 THEN '🚨 CRITICAL: Trigger 25% Instant Retention Code + Priority AM Outreach'
    WHEN churn_probability >= 0.50 THEN '⚠️ HIGH RISK: Dispatch Free Express Shipping Voucher'
    WHEN churn_probability >= 0.25 THEN '🟡 MODERATE: Send Personalized Re-engagement Newsletter'
    ELSE '🟢 HEALTHY: Standard Loyalty Nurturing'
  END AS automated_retention_action,
  CURRENT_TIMESTAMP() AS calculation_timestamp
FROM
  customer_predictions;

-- Verification query: Inspect high-priority retention targets
SELECT
  customer_id,
  customer_name,
  loyalty_tier,
  churn_probability,
  churn_risk_tier,
  automated_retention_action,
  calculation_timestamp
FROM
  `${GCP_PROJECT_ID}.${BIGQUERY_DATASET}.customer_churn_risk`
ORDER BY
  churn_probability DESC
LIMIT 100;


