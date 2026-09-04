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

output "demo_principals" {
  description = "The email addresses of the demo IAM service accounts."
  value = {
    demo1 = google_service_account.demo_principals["demo1-user"].email
    demo2 = google_service_account.demo_principals["demo2-user"].email
  }
output "bigquery_scheduled_query_name" {
  description = "The name/ID of the BigQuery Data Transfer scheduled query for daily churn analysis."
  value       = var.enable_scheduled_query ? google_bigquery_data_transfer_config.daily_churn_analysis[0].name : "disabled"
}

output "cloud_run_job_name" {
  description = "The name of the Cloud Run Job for Reverse-ETL churn sync."
  value       = var.enable_churn_sync_job ? google_cloud_run_v2_job.churn_sync_job[0].name : "disabled"
}

output "cloud_scheduler_sync_job_name" {
  description = "The name of the Cloud Scheduler job for Reverse-ETL churn sync."
  value       = var.enable_churn_sync_job ? google_cloud_scheduler_job.churn_sync_scheduler[0].name : "disabled"
}

output "artifact_registry_pipeline_repo" {
  description = "The Artifact Registry Docker repository for pipeline images."
  value       = var.enable_churn_sync_job ? google_artifact_registry_repository.pipeline_repo[0].name : "disabled"
}
