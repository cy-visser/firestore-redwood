# ==============================================================================
# Cloud Dataflow Streaming Pipeline Orchestration
# ==============================================================================

resource "terraform_data" "dataflow_pipeline" {
  triggers_replace = [
    var.project_id,
    var.region,
    var.firestore_database_id,
    var.firestore_collection,
    var.bigquery_dataset_id,
    var.bigquery_cdc_table_id,
    var.dataflow_job_name,
    google_storage_bucket.redwood_bucket.name,
    google_service_account.pipeline_sa.email,
    terraform_data.firestore_database.id,
    filesha256("${path.module}/../dataflow/dataflow_firestore_to_bigquery_beam.py")
  ]

  provisioner "local-exec" {
    command = "${path.module}/scripts/launch_dataflow_job.sh '${var.project_id}' '${var.region}' '${var.firestore_database_id}' '${google_bigquery_dataset.redwood_retail.dataset_id}' '${google_storage_bucket.redwood_bucket.name}' '${google_service_account.pipeline_sa.email}' '${var.dataflow_job_name}' '${var.firestore_collection}' '${google_bigquery_table.orders_cdc.table_id}'"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "${path.module}/scripts/cleanup_dataflow_job.sh '${self.triggers_replace[0]}' '${self.triggers_replace[1]}' '${self.triggers_replace[6]}'"
  }

  depends_on = [
    google_project_service.services["dataflow.googleapis.com"],
    google_project_service.services["compute.googleapis.com"],
    terraform_data.firestore_database,
    google_bigquery_table.orders_cdc,
    google_storage_bucket.redwood_bucket,
    google_project_iam_member.sa_firestore_owner,
    google_project_iam_member.sa_dataflow_worker,
    google_project_iam_member.sa_dataflow_admin,
    google_project_iam_member.sa_bigquery_editor,
    google_project_iam_member.sa_bigquery_job_user,
    google_project_iam_member.sa_storage_admin,
    google_compute_network.dataflow_network,
    google_compute_subnetwork.dataflow_subnet,
    google_compute_router_nat.nat,
    google_compute_firewall.dataflow_internal
  ]
}
