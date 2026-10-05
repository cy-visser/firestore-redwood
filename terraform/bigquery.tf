resource "google_bigquery_dataset" "redwood_retail" {
  project                    = var.project_id
  dataset_id                 = var.bigquery_dataset_id
  friendly_name              = "Redwood Retail Dataset"
  description                = "Retail orders and customers mirrored from Firestore Enterprise via Eventarc change data capture"
  location                   = var.region
  delete_contents_on_destroy = true

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_project_service.services["bigquery.googleapis.com"]
  ]
}

resource "google_bigquery_table" "orders_cdc" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.redwood_retail.dataset_id
  table_id            = var.bigquery_cdc_table_id
  deletion_protection = false

  description = "Append-only change ledger replicated from the Firestore orders change stream. Current state lives in retail_current."

  clustering = ["order_id", "customer_id", "order_status"]

  time_partitioning {
    type  = "DAY"
    field = "change_timestamp"
  }

  schema = jsonencode([
    {
      name        = "order_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique Order Identifier"
    },
    {
      name        = "operation_type"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Change Stream Operation Type (insert, update, replace, delete)"
    },
    {
      name        = "customer_id"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer ID"
    },
    {
      name        = "customer_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Full Name / Company"
    },
    {
      name        = "customer_email"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Contact Email"
    },
    {
      name        = "customer_segment"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Segment Category"
    },
    {
      name        = "order_status"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Current Order Status (PENDING, PROCESSING, SHIPPED, DELIVERED, CANCELLED)"
    },
    {
      name        = "payment_status"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Payment Status (PENDING, AUTHORIZED, SETTLED, REFUNDED)"
    },
    {
      name        = "payment_method"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Payment Method Used"
    },
    {
      name        = "currency"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Order Currency Code"
    },
    {
      name        = "grand_total"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Grand Total Order Value"
    },
    {
      name        = "subtotal"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Order Subtotal before tax and shipping"
    },
    {
      name        = "profit_margin"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Calculated Order Profit Margin"
    },
    {
      name        = "change_timestamp"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when the change event occurred"
    },
    {
      name        = "document_data"
      type        = "JSON"
      mode        = "NULLABLE"
      description = "Full Raw Document Payload in JSON format"
    }
  ])

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_bigquery_dataset.redwood_retail
  ]
}

# ------------------------------------------------------------------------------
# The two CDC mirror tables.
#
# Until now these existed only because the CDC container created them at
# startup (cdc_service/main.py::_bootstrap -> CdcSink.ensure_tables), and that
# bootstrap is deliberately non-fatal: when the DDL failed the service reported
# /healthz degraded and kept serving, so the first visible symptom was the
# scheduled churn query failing on a table that was never there. Declaring them
# here makes the tables part of the deployment rather than a side effect of a
# container start.
#
# ensure_tables stays as it is. It issues CREATE TABLE IF NOT EXISTS, which is a
# no-op once Terraform owns the table, so the two cannot fight.
#
# Everything below has to stay identical to the TableSpecs in
# cdc_service/schemas.py, because that is what create_table_ddl would otherwise
# produce and what the Storage Write API writes against:
#   - the non-enforced primary key is the whole reason the CDC path works; with
#     no key BigQuery ignores _CHANGE_TYPE and every UPSERT lands as a duplicate
#     row instead of replacing the previous state,
#   - every column is NULLABLE and neither table is partitioned, matching the
#     DDL exactly, so a table created by either route is the same table.
# ------------------------------------------------------------------------------
resource "google_bigquery_table" "orders_current" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.redwood_retail.dataset_id
  table_id            = "${var.firestore_collection}_current"
  deletion_protection = false

  description = "Live mirror of Firestore orders, maintained by CDC UPSERT/DELETE"

  clustering = ["customer_id", "order_status"]

  table_constraints {
    primary_key {
      columns = ["order_id"]
    }
  }

  schema = jsonencode([
    { name = "order_id", type = "STRING", mode = "NULLABLE" },
    { name = "customer_id", type = "STRING", mode = "NULLABLE" },
    { name = "customer_name", type = "STRING", mode = "NULLABLE" },
    { name = "customer_email", type = "STRING", mode = "NULLABLE" },
    { name = "customer_segment", type = "STRING", mode = "NULLABLE" },
    { name = "order_status", type = "STRING", mode = "NULLABLE" },
    { name = "payment_status", type = "STRING", mode = "NULLABLE" },
    { name = "payment_method", type = "STRING", mode = "NULLABLE" },
    { name = "currency", type = "STRING", mode = "NULLABLE" },
    { name = "subtotal", type = "FLOAT", mode = "NULLABLE" },
    { name = "tax_amount", type = "FLOAT", mode = "NULLABLE" },
    { name = "shipping_fee", type = "FLOAT", mode = "NULLABLE" },
    { name = "discount_total", type = "FLOAT", mode = "NULLABLE" },
    { name = "grand_total", type = "FLOAT", mode = "NULLABLE" },
    { name = "profit_margin", type = "FLOAT", mode = "NULLABLE" },
    { name = "total_spend_90d", type = "FLOAT", mode = "NULLABLE" },
    { name = "lifetime_spend", type = "FLOAT", mode = "NULLABLE" },
    { name = "avg_order_value", type = "FLOAT", mode = "NULLABLE" },
    { name = "purchase_frequency_monthly", type = "FLOAT", mode = "NULLABLE" },
    { name = "days_since_last_purchase", type = "INTEGER", mode = "NULLABLE" },
    { name = "orders_count_last_12m", type = "INTEGER", mode = "NULLABLE" },
    { name = "login_frequency_monthly", type = "INTEGER", mode = "NULLABLE" },
    { name = "avg_session_duration_minutes", type = "FLOAT", mode = "NULLABLE" },
    { name = "app_engagement_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "app_sessions_last_30d", type = "INTEGER", mode = "NULLABLE" },
    { name = "cart_abandonment_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "abandoned_cart_value_90d", type = "FLOAT", mode = "NULLABLE" },
    { name = "support_tickets_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "open_support_tickets_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "complaints_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "return_rate_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "sentiment_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "has_active_complaint", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "primary_complaint_reason", type = "STRING", mode = "NULLABLE" },
    { name = "feedback_rating", type = "INTEGER", mode = "NULLABLE" },
    { name = "feedback_channel", type = "STRING", mode = "NULLABLE" },
    { name = "loyalty_tier", type = "STRING", mode = "NULLABLE" },
    { name = "is_loyalty_member", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "account_age_days", type = "INTEGER", mode = "NULLABLE" },
    { name = "shipping_city", type = "STRING", mode = "NULLABLE" },
    { name = "shipping_country_code", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "updated_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "change_timestamp", type = "TIMESTAMP", mode = "NULLABLE" }
  ])

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_bigquery_dataset.redwood_retail
  ]
}

resource "google_bigquery_table" "customers_current" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.redwood_retail.dataset_id
  table_id            = "${var.firestore_customers_collection}_current"
  deletion_protection = false

  description = "Live mirror of Firestore customer profiles"

  clustering = ["customer_segment", "loyalty_tier"]

  table_constraints {
    primary_key {
      columns = ["customer_id"]
    }
  }

  # The friction columns sit after change_timestamp rather than next to the
  # other support metrics: the table already exists in every deployed
  # environment, and BigQuery only accepts new columns appended to the end of a
  # live schema. Reordering would make the update fail instead of applying.
  schema = jsonencode([
    { name = "customer_id", type = "STRING", mode = "NULLABLE" },
    { name = "customer_name", type = "STRING", mode = "NULLABLE" },
    { name = "customer_email", type = "STRING", mode = "NULLABLE" },
    { name = "customer_segment", type = "STRING", mode = "NULLABLE" },
    { name = "loyalty_tier", type = "STRING", mode = "NULLABLE" },
    { name = "is_loyalty_member", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "account_age_days", type = "INTEGER", mode = "NULLABLE" },
    { name = "lifetime_spend", type = "FLOAT", mode = "NULLABLE" },
    { name = "orders_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "last_order_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "days_since_last_purchase", type = "INTEGER", mode = "NULLABLE" },
    { name = "login_frequency_monthly", type = "INTEGER", mode = "NULLABLE" },
    { name = "avg_session_duration_minutes", type = "FLOAT", mode = "NULLABLE" },
    { name = "app_engagement_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "cart_abandonment_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "support_tickets_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "open_support_tickets_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "complaints_count", type = "INTEGER", mode = "NULLABLE" },
    { name = "return_rate_percent", type = "FLOAT", mode = "NULLABLE" },
    { name = "iam_principal", type = "STRING", mode = "NULLABLE" },
    { name = "is_demo_persona", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "updated_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "change_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "total_spend_90d", type = "FLOAT", mode = "NULLABLE" },
    { name = "return_frequency", type = "INTEGER", mode = "NULLABLE" },
    { name = "sentiment_score", type = "FLOAT", mode = "NULLABLE" },
    { name = "has_active_complaint", type = "BOOLEAN", mode = "NULLABLE" },
    { name = "primary_complaint_reason", type = "STRING", mode = "NULLABLE" },
    { name = "recent_friction_event", type = "STRING", mode = "NULLABLE" },
    { name = "feedback_rating", type = "INTEGER", mode = "NULLABLE" }
  ])

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_bigquery_dataset.redwood_retail
  ]
}

resource "google_bigquery_table" "customer_churn_risk" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.redwood_retail.dataset_id
  table_id            = "customer_churn_risk"
  deletion_protection = false

  description = "Materialized daily batch churn risk scores computed by BigQuery ML to eliminate OLTP login latency (SDD Section 1.2)"

  lifecycle {
    ignore_changes = [
      clustering,
      time_partitioning,
      schema
    ]
  }

  clustering = ["customer_id", "churn_risk_tier"]

  time_partitioning {
    type  = "DAY"
    field = "calculation_timestamp"
  }

  schema = jsonencode([
    {
      name        = "customer_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Unique Customer Identifier"
    },
    {
      name        = "customer_name"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Full Name"
    },
    {
      name        = "customer_email"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Contact Email"
    },
    {
      name        = "customer_segment"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Segment Classification"
    },
    {
      name        = "loyalty_tier"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Customer Loyalty Program Tier"
    },
    {
      name        = "predicted_is_churned"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Binary churn classification from BQML"
    },
    {
      name        = "churn_probability"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Model calculated churn probability between 0.0 and 1.0"
    },
    {
      name        = "churn_risk_tier"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Calibrated risk tier (LOW, MODERATE, HIGH, CRITICAL)"
    },
    {
      name        = "total_spend_90d"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Total purchase spend in preceding 90 days"
    },
    {
      name        = "days_since_last_purchase"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Inactivity days since most recent purchase"
    },
    {
      name        = "cart_abandonment_count"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Number of abandoned shopping carts in last 90 days"
    },
    {
      name        = "support_tickets_count"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Customer service tickets submitted"
    },
    {
      name        = "sentiment_score"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Customer sentiment score (-1.0 to +1.0)"
    },
    {
      name        = "automated_retention_action"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Prescribed retention intervention recommendation"
    },
    {
      name        = "calculation_timestamp"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Timestamp when batch inference completed"
    }
  ])

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "loyalty_agent"
  }

  depends_on = [
    google_bigquery_dataset.redwood_retail
  ]
}
