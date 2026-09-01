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
