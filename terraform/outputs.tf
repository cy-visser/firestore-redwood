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
  value       = google_firestore_database.database.name
}

output "firestore_edition" {
  description = "The edition of the Firestore database."
  value       = google_firestore_database.database.database_edition
}

output "firestore_pitr_enabled" {
  description = "Whether PITR is enabled on the Firestore database."
  value       = google_firestore_database.database.point_in_time_recovery_enablement
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

output "sample_order_id" {
  description = "The ID of the seeded sample order in Firestore."
  value       = "ORD-2026-98471A-X9"
}

output "firestore_change_stream_name" {
  description = "The Firestore Change Stream identifier."
  value       = var.enable_change_stream ? var.change_stream_name : "DISABLED"
}

output "firestore_change_stream_collection_group" {
  description = "The target collection group of the Firestore Change Stream."
  value       = var.enable_change_stream ? var.change_stream_collection_group : "DISABLED"
}

output "firestore_change_stream_retention" {
  description = "The retention duration of the Firestore Change Stream."
  value       = var.enable_change_stream ? var.change_stream_retention : "DISABLED"
}
