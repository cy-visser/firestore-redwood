variable "project_id" {
  description = "The Google Cloud Project ID to deploy resources in. Supplied via TF_VAR_project_id from .env (GCP_PROJECT_ID); intentionally has no default so a misconfigured environment fails fast instead of targeting the wrong project."
  type        = string

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must be set. Export TF_VAR_project_id or set GCP_PROJECT_ID in .env."
  }
}

variable "region" {
  description = "The primary single region for all resources (Firestore, BigQuery, GCS, Cloud Run, Eventarc)."
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
  description = "The account ID for the dedicated Service Account. Retains the dataflow-era name so existing IAM grants and state do not churn."
  type        = string
  default     = "dataflow-redwood-sa"
}

variable "service_account_display_name" {
  description = "Display name for the dedicated Service Account."
  type        = string
  default     = "Redwood Retail Pipeline Service Account"
}

variable "firestore_collection" {
  description = "The Firestore collection to stream to BigQuery."
  type        = string
  default     = "retail"
}

variable "trace_document_id_prefix" {
  description = "Only replicated documents whose id starts with this prefix get a latency trace written for the console. Defaults to the mobile app's order id prefix so a backfill of the seeded dataset does not write a trace per document. Set to \"\" to disable order tracing."
  type        = string
  default     = "ORD-26-MOB-"
}

variable "demo_principal_ids" {
  description = "IAM service account IDs representing the demo client users. demo1-user is seeded as a healthy/low-churn customer, demo2-user as an at-risk/high-churn customer."
  type        = list(string)
  default     = ["demo1-user", "demo2-user"]
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

# The container-based Agent Engine variables that used to live here configured
# the six-engine A2A mesh and were removed with it. The single agent is
# deployed from scripts/deploy_agent_engine.py, which ships the Python object
# rather than a container, so there is no image tag, CPU or memory to set from
# Terraform. Its display name is agent_display_name in agent_bridge.tf, where
# the bridge that has to agree with it also lives.
#
# reasoning_model and bigquery_predictions_table_id were removed for a
# different reason: no resource ever read either of them. The model is chosen
# by the REASONING_MODEL environment variable in loyalty_agent/config.py, so a
# Terraform variable appearing to control it was worse than having none.

