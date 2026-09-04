# Dedicated Service Account for Dataflow Pipeline & Applications
resource "google_service_account" "pipeline_sa" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = var.service_account_display_name

  depends_on = [
    google_project_service.services["iam.googleapis.com"]
  ]
}

# Service Account Firestore Owner Permission (for Native Firestore & Datastore Access)
resource "google_project_iam_member" "sa_firestore_owner" {
  project = var.project_id
  role    = "roles/datastore.owner"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account BigQuery Data Editor (Read/Write to BigQuery)
resource "google_project_iam_member" "sa_bigquery_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account BigQuery Job User (Query/Job Execution)
resource "google_project_iam_member" "sa_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account Dataflow Worker Role
resource "google_project_iam_member" "sa_dataflow_worker" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account Dataflow Admin Role
resource "google_project_iam_member" "sa_dataflow_admin" {
  project = var.project_id
  role    = "roles/dataflow.admin"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account GCS Object Admin Role (for Dataflow staging/temp buckets)
resource "google_project_iam_member" "sa_storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Dedicated Demo Service Accounts for Mobile Client Users
resource "google_service_account" "demo_principals" {
  for_each     = toset(["demo1-user", "demo2-user"])
  project      = var.project_id
  account_id   = each.key
  display_name = "Redwood Retail Demo Principal ${each.key}"

  depends_on = [
    google_project_service.services["iam.googleapis.com"]
  ]
}

# Service Account Firestore User Role for Demo Principals
resource "google_project_iam_member" "demo_principals_firestore" {
  for_each = toset(["demo1-user", "demo2-user"])
  project  = var.project_id
  role     = "roles/datastore.user"
  member   = "serviceAccount:${google_service_account.demo_principals[each.key].email}"
}
