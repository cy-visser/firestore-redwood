variable "project_id" {
  description = "The Google Cloud Project ID to deploy resources in."
  type        = string
  default     = "redwood-retail-949ec9"
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

variable "bigquery_predictions_table_id" {
  description = "The BigQuery table for customer churn predictions."
  type        = string
  default     = "customer_churn_risk"
}


variable "reasoning_model" {
  description = "The Gemini model for the autonomous agent reasoning engine."
  type        = string
  default     = "gemini-3.8-flash"
}

variable "enable_artifact_registry" {
  description = "Whether to deploy the Artifact Registry repository."
  type        = bool
  default     = true
}

variable "enable_security_rules" {
  description = "Whether to deploy Firestore security rules via Terraform."
  type        = bool
  default     = false
}

variable "enable_agent_engine" {
  description = "Whether to deploy the Vertex AI Agent Engine (Reasoning Engine) via BYOC container (deprecated in favor of native Agent Runtime packaging)."
  type        = bool
  default     = false
}


variable "agent_engine_display_name" {
  description = "Display name for the Vertex AI Agent Engine."
  type        = string
  default     = "redwood-retention-orchestrator"
}

variable "agent_engine_description" {
  description = "Description for the Vertex AI Agent Engine."
  type        = string
  default     = "Redwood Retail Multi-Agent Retention Platform powered by Vertex AI Agent Runtime and A2A protocol"
}

variable "agent_image_tag" {
  description = "Container image tag for the Agent Runtime container image."
  type        = string
  default     = "latest"
}

variable "agent_min_instances" {
  description = "Minimum number of Agent Runtime instances."
  type        = number
  default     = 1
}

variable "agent_max_instances" {
  description = "Maximum number of Agent Runtime instances."
  type        = number
  default     = 5
}

variable "agent_cpu" {
  description = "CPU allocation for the Agent Runtime container."
  type        = string
  default     = "2"
}

variable "agent_memory" {
  description = "Memory allocation for the Agent Runtime container."
  type        = string
  default     = "4Gi"
}
