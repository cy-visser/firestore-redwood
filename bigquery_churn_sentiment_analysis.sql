-- ==============================================================================
-- BigQuery Analytics Workbook: Redwood Retail Churn & Sentiment Analysis
-- Dataset: `elevate-cyvisser.redwood_retail`
-- Replicated in real-time from Firestore Enterprise via Cloud Datastream
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Executive Dashboard: Order Volumes, Financials & Churn Health Summary
-- ------------------------------------------------------------------------------
SELECT
  customerSegment,
  COUNT(1) AS total_orders,
  ROUND(SUM(financials.grandTotal), 2) AS total_revenue_eur,
  ROUND(AVG(financials.grandTotal), 2) AS avg_order_value_eur,
  ROUND(AVG(churnAnalysis.churnRiskScore), 3) AS avg_churn_risk,
  COUNTIF(churnAnalysis.churnCategory = 'CRITICAL_RISK') AS critical_risk_orders,
  COUNTIF(customerFeedback.hasActiveComplaint) AS total_complaints,
  ROUND(AVG(customerFeedback.rating), 2) AS avg_customer_rating
FROM
  `elevate-cyvisser.redwood_retail.orders`
GROUP BY
  customerSegment
ORDER BY
  total_revenue_eur DESC;


-- ------------------------------------------------------------------------------
-- 2. Customer RFM & Churn Segmentation (Recency, Frequency, Monetary)
-- ------------------------------------------------------------------------------
WITH customer_aggregates AS (
  SELECT
    customerId,
    ANY_VALUE(customerName) AS customerName,
    ANY_VALUE(customerSegment) AS customerSegment,
    COUNT(1) AS order_count,
    ROUND(SUM(financials.grandTotal), 2) AS total_spend,
    MIN(churnAnalysis.daysSinceLastOrder) AS days_since_last_order,
    ROUND(AVG(churnAnalysis.returnRatePercent), 1) AS avg_return_rate,
    COUNTIF(customerFeedback.hasActiveComplaint) AS complaint_count,
    ROUND(AVG(churnAnalysis.churnRiskScore), 3) AS avg_churn_score
  FROM
    `elevate-cyvisser.redwood_retail.orders`
  GROUP BY
    customerId
)
SELECT
  customerId,
  customerName,
  customerSegment,
  order_count,
  total_spend,
  days_since_last_order,
  avg_return_rate,
  complaint_count,
  avg_churn_score,
  CASE
    WHEN avg_churn_score >= 0.80 THEN '🚨 CRITICAL CHURN RISK'
    WHEN avg_churn_score >= 0.60 THEN '⚠️ HIGH RISK'
    WHEN avg_churn_score >= 0.35 THEN '🟡 MODERATE'
    ELSE '🟢 LOYAL & HEALTHY'
  END AS retention_action_priority
FROM
  customer_aggregates
ORDER BY
  avg_churn_score DESC,
  total_spend DESC
LIMIT 100;


-- ------------------------------------------------------------------------------
-- 3. Customer Sentiment Analysis by Channel & Complaint Driver
-- ------------------------------------------------------------------------------
SELECT
  customerFeedback.channel AS feedback_channel,
  customerFeedback.primaryComplaintReason AS complaint_driver,
  COUNT(1) AS feedback_count,
  ROUND(AVG(customerFeedback.rating), 2) AS avg_rating,
  ROUND(AVG(churnAnalysis.churnRiskScore), 3) AS associated_churn_risk,
  ARRAY_AGG(customerFeedback.feedbackText LIMIT 2) AS sample_customer_feedback
FROM
  `elevate-cyvisser.redwood_retail.orders`
WHERE
  customerFeedback.hasActiveComplaint = TRUE
GROUP BY
  feedback_channel,
  complaint_driver
ORDER BY
  feedback_count DESC;


-- ------------------------------------------------------------------------------
-- 4. Autonomous Retention Agent Live Feed
-- Identifies high-risk enterprise accounts requiring real-time discount offers
-- ------------------------------------------------------------------------------
SELECT
  orderId,
  customerId,
  customerName,
  customerEmail,
  customerSegment,
  financials.grandTotal AS cart_total_eur,
  customerFeedback.feedbackText AS trigger_complaint_text,
  churnAnalysis.churnRiskScore AS churn_score,
  churnAnalysis.retentionShieldStatus,
  suggestedOffer.offerId AS proposed_offer_id,
  suggestedOffer.discountPercent AS proposed_discount_pct,
  suggestedOffer.description AS offer_description,
  updatedAt
FROM
  `elevate-cyvisser.redwood_retail.orders`
WHERE
  churnAnalysis.retentionShieldStatus = 'ELIGIBLE_FOR_OFFER'
  AND churnAnalysis.churnRiskScore >= 0.75
ORDER BY
  churnAnalysis.churnRiskScore DESC,
  financials.grandTotal DESC
LIMIT 50;
