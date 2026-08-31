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

variable "enable_change_stream" {
  description = "Whether to provision and manage Firestore MongoDB Change Streams."
  type        = bool
  default     = true
}

variable "change_stream_name" {
  description = "Identifier for the Firestore change stream."
  type        = string
  default     = "orders-stream"
}

variable "change_stream_collection_group" {
  description = "Target collection group for the Firestore change stream."
  type        = string
  default     = "orders"
}

variable "change_stream_retention" {
  description = "Retention period for the change stream (between 1d and 7d)."
  type        = string
  default     = "7d"
}

variable "dataflow_job_name" {
  description = "Name for the Dataflow streaming CDC replication job."
  type        = string
  default     = "firestore-orders-to-bigquery"
}
