# Dedicated Service Account for the CDC service, churn jobs and the agent.
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

# Service Account GCS Object Admin Role (build sources, exports)
resource "google_project_iam_member" "sa_storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# ------------------------------------------------------------------------------
# Demo Principals: dedicated IAM service accounts representing the two mobile
# client users (demo1 = healthy/low churn risk, demo2 = at-risk/high churn risk).
# Restored from commit d1bf104^, where they were removed unintentionally.
# ------------------------------------------------------------------------------
resource "google_service_account" "demo_principals" {
  for_each     = toset(var.demo_principal_ids)
  project      = var.project_id
  account_id   = each.key
  display_name = "Redwood Retail Demo Principal ${each.key}"

  depends_on = [
    google_project_service.services["iam.googleapis.com"]
  ]
}

# Demo principals need Firestore read/write to create sessions and read offers.
resource "google_project_iam_member" "demo_principals_firestore" {
  for_each = toset(var.demo_principal_ids)
  project  = var.project_id
  role     = "roles/datastore.user"
  member   = "serviceAccount:${google_service_account.demo_principals[each.key].email}"
}

# Service Account Vertex AI User Role (for Gemini reasoning in Loyalty Agent)
resource "google_project_iam_member" "sa_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Service Account Artifact Registry Reader
resource "google_project_iam_member" "sa_artifactregistry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Grant Artifact Registry Reader to Agent Runtime Service Agents
resource "google_project_iam_member" "re_service_agent_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"

  depends_on = [
    google_project_service_identity.aiplatform_sa
  ]
}

resource "google_project_iam_member" "re_dedicated_service_agent_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

  depends_on = [
    google_project_service_identity.aiplatform_sa
  ]
}

# Allow Agent Runtime Service Agents to act as the pipeline service account
resource "google_service_account_iam_member" "agent_engine_sa_user" {
  service_account_id = google_service_account.pipeline_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"

  depends_on = [
    google_project_service_identity.aiplatform_sa
  ]
}

resource "google_service_account_iam_member" "re_dedicated_agent_engine_sa_user" {
  service_account_id = google_service_account.pipeline_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

  depends_on = [
    google_project_service_identity.aiplatform_sa
  ]
}

# Regional Cloud Build uses the default Compute Engine service account in newer
# projects, which starts with zero IAM roles when automatic IAM grants for
# default service accounts are disabled.
resource "google_project_iam_member" "compute_sa_cloudbuild_builder" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"

  depends_on = [
    google_project_service_identity.compute_sa
  ]
}

