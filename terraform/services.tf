locals {
  required_services = [
    "firestore.googleapis.com",
    "dataflow.googleapis.com",
    "compute.googleapis.com",
    "bigquery.googleapis.com",
    "bigquerystorage.googleapis.com",
    "storage.googleapis.com",
    "storage-component.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com"
  ]
}

resource "google_project_service" "services" {
  for_each = toset(local.required_services)
  project  = var.project_id
  service  = each.key

  disable_on_destroy         = false
  disable_dependent_services = false
}

# Provision Google-managed Service Agents before IAM bindings are applied
resource "google_project_service_identity" "aiplatform_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"]
  ]
}

resource "google_project_service_identity" "dataflow_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "dataflow.googleapis.com"

  depends_on = [
    google_project_service.services["dataflow.googleapis.com"]
  ]
}

