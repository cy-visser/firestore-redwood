resource "google_bigquery_dataset" "redwood_retail" {
  project                    = var.project_id
  dataset_id                 = var.bigquery_dataset_id
  friendly_name              = "Redwood Retail Dataset"
  description                = "Real-time retail orders dataset mirrored from Firestore Enterprise via Dataflow CDC"
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

  description = "Real-time Change Data Capture (CDC) table replicated from Firestore orders change stream"

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

resource "google_bigquery_table" "customer_churn_risk" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.redwood_retail.dataset_id
  table_id            = "customer_churn_risk"
  deletion_protection = false

  description = "Materialized daily batch churn risk scores computed by BigQuery ML to eliminate OLTP login latency (SDD Section 1.2)"

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
