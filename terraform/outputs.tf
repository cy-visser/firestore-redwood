output "project_id" {
  description = "The Google Cloud project ID."
  value       = var.project_id
}

output "region" {
  description = "The deployment region."
  value       = var.region
}

output "firestore_database_id" {
  description = "The Firestore database ID."
  value       = var.firestore_database_id
}

output "firestore_edition" {
  description = "The edition of the Firestore database."
  value       = var.firestore_edition
}

output "firestore_pitr_enabled" {
  description = "Whether PITR is enabled on the Firestore database."
  value       = var.enable_pitr ? "POINT_IN_TIME_RECOVERY_ENABLED" : "POINT_IN_TIME_RECOVERY_DISABLED"
}

output "firestore_collection" {
  description = "The Firestore collection being replicated."
  value       = var.firestore_collection
}

output "bigquery_dataset_id" {
  description = "The BigQuery dataset ID for Redwood Retail."
  value       = google_bigquery_dataset.redwood_retail.dataset_id
}

output "bigquery_dataset_location" {
  description = "The location of the BigQuery dataset."
  value       = google_bigquery_dataset.redwood_retail.location
}

output "bigquery_orders_cdc_table" {
  description = "The full BigQuery CDC table ID."
  value       = "${var.project_id}.${google_bigquery_dataset.redwood_retail.dataset_id}.${google_bigquery_table.orders_cdc.table_id}"
}

output "bigquery_predictions_table" {
  description = "The full BigQuery churn predictions table ID."
  value       = "${var.project_id}.${google_bigquery_dataset.redwood_retail.dataset_id}.${var.bigquery_predictions_table_id}"
}

output "storage_bucket_name" {
  description = "The Cloud Storage bucket name for Dataflow temp and staging."
  value       = google_storage_bucket.redwood_bucket.name
}

output "storage_bucket_url" {
  description = "The Cloud Storage bucket URL."
  value       = google_storage_bucket.redwood_bucket.url
}

output "service_account_email" {
  description = "The email of the dedicated Service Account."
  value       = google_service_account.pipeline_sa.email
}

output "dataflow_job_name" {
  description = "The Dataflow CDC replication streaming job name."
  value       = var.dataflow_job_name
}

output "bigquery_scheduled_query_name" {
  description = "The name/ID of the BigQuery Data Transfer scheduled query for daily churn analysis."
  value       = var.enable_scheduled_query ? google_bigquery_data_transfer_config.daily_churn_analysis[0].name : "disabled"
}
