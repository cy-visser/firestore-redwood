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

output "artifact_registry_repo" {
  description = "The Artifact Registry repository name for pipeline and agent container images."
  value       = var.enable_artifact_registry ? google_artifact_registry_repository.pipeline_repo[0].name : "DISABLED"
}

# Discrete A2A Reasoning Engines
output "cooldown_agent_name" {
  description = "The full resource name of the Standalone Cooldown Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.cooldown_agent[0].name : "DISABLED"
}

output "churn_agent_name" {
  description = "The full resource name of the Standalone Churn Intelligence Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.churn_agent[0].name : "DISABLED"
}

output "friction_agent_name" {
  description = "The full resource name of the Standalone Customer Friction Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.friction_agent[0].name : "DISABLED"
}

output "synthesis_agent_name" {
  description = "The full resource name of the Standalone Offer Synthesis Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.synthesis_agent[0].name : "DISABLED"
}

output "fulfillment_agent_name" {
  description = "The full resource name of the Standalone Offer Fulfillment Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.fulfillment_agent[0].name : "DISABLED"
}

output "orchestrator_agent_name" {
  description = "The full resource name of the Standalone Retention Orchestrator Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.retention_orchestrator[0].name : "DISABLED"
}

output "agent_engine_id" {
  description = "The resource ID of the deployed Retention Orchestrator Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.retention_orchestrator[0].id : "DISABLED"
}

output "agent_engine_name" {
  description = "The full resource name of the deployed Retention Orchestrator Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.retention_orchestrator[0].name : "DISABLED"
}

output "agent_engine_display_name" {
  description = "Display name of the Retention Orchestrator Agent Engine."
  value       = var.enable_agent_engine ? google_vertex_ai_reasoning_engine.retention_orchestrator[0].display_name : "DISABLED"
}

output "agent_engine_image" {
  description = "Artifact Registry container image URI for the Agent Runtime container."
  value       = var.enable_agent_engine ? "${var.region}-docker.pkg.dev/${var.project_id}/${var.enable_artifact_registry ? google_artifact_registry_repository.pipeline_repo[0].repository_id : "pipeline-images"}/loyalty-agent-runtime:${var.agent_image_tag}" : "DISABLED"
}

output "agent_engine_card" {
  description = "The A2A Agent Card registered with the Retention Orchestrator Agent Engine."
  value       = var.enable_agent_engine ? local.agent_card : "DISABLED"
}

output "event_bridge_name" {
  description = "The Cloud Run service name of the Event Bridge."
  value       = var.enable_event_bridge ? google_cloud_run_v2_service.event_bridge[0].name : "DISABLED"
}

output "event_bridge_url" {
  description = "The service URL of the Event Bridge."
  value       = var.enable_event_bridge ? google_cloud_run_v2_service.event_bridge[0].uri : "DISABLED"
}

