variable "project_id" {
  description = "The Google Cloud Project ID to deploy resources in."
  type        = string
  default     = "elevate-cyvisser"
}

variable "region" {
  description = "The primary single region for all resources (Firestore, BigQuery, GCS, Dataflow)."
  type        = string
  default     = "europe-west4"
}

variable "firestore_database_id" {
  description = "The Firestore database ID to create."
  type        = string
  default     = "redwood"
}

variable "firestore_edition" {
  description = "The Firestore database edition (ENTERPRISE or STANDARD)."
  type        = string
  default     = "ENTERPRISE"
}

variable "enable_pitr" {
  description = "Whether to enable Point-In-Time Recovery (PITR) on Firestore."
  type        = bool
  default     = true
}

variable "bigquery_dataset_id" {
  description = "The BigQuery dataset ID for retail orders streaming sync."
  type        = string
  default     = "redwood_retail"
}

variable "bigquery_cdc_table_id" {
  description = "The BigQuery table ID for real-time CDC events."
  type        = string
  default     = "retail_cdc"
}

variable "bigquery_predictions_table_id" {
  description = "The BigQuery table ID for persisted BQML churn predictions and retention actions."
  type        = string
  default     = "customer_churn_predictions"
}

variable "bigquery_historical_view_id" {
  description = "The BigQuery feature engineering view ID."
  type        = string
  default     = "customer_historical_data"
}

variable "bigquery_churn_model_id" {
  description = "The BigQuery ML customer churn model ID."
  type        = string
  default     = "customer_churn_model"
}

variable "enable_scheduled_query" {
  description = "Whether to create a BigQuery Scheduled Query for automated daily churn analysis."
  type        = bool
  default     = true
}

variable "scheduled_query_schedule" {
  description = "Cron or frequency expression for the daily BigQuery scheduled query (e.g. 'every 24 hours' or '0 2 * * *')."
  type        = string
  default     = "every 24 hours"
}

variable "gcs_bucket_name_prefix" {
  description = "Prefix for the Cloud Storage bucket name."
  type        = string
  default     = "redwood-retail"
}

variable "service_account_id" {
  description = "The account ID for the dedicated Service Account."
  type        = string
  default     = "dataflow-redwood-sa"
}

variable "service_account_display_name" {
  description = "Display name for the dedicated Service Account."
  type        = string
  default     = "Dataflow Redwood Retail Service Account"
}

variable "firestore_collection" {
  description = "The Firestore collection to stream to BigQuery."
  type        = string
  default     = "retail"
}

variable "dataflow_job_name" {
  description = "Name for the Dataflow streaming CDC replication job."
  type        = string
  default     = "firestore-retail-to-bigquery"
}

variable "enable_churn_sync_job" {
  description = "Whether to create the Cloud Run Job and Cloud Scheduler for daily Reverse-ETL churn sync."
  type        = bool
  default     = true
}

variable "churn_sync_schedule" {
  description = "Cron or frequency expression for the daily Cloud Run sync job (e.g. 'every 24 hours' or '0 3 * * *')."
  type        = string
  default     = "every 24 hours"
}

variable "churn_sync_image" {
  description = "Container image URI for Cloud Run churn sync job. If empty, defaults to Artifact Registry repository image."
  type        = string
  default     = ""
}
