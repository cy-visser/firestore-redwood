locals {
  required_services = [
    "firestore.googleapis.com",
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
    "cloudbuild.googleapis.com",
    # Backs BigQuery scheduled queries, which is how the churn pipeline is
    # re-run daily. The name is historical; a scheduled query is modelled as a
    # transfer config.
    "bigquerydatatransfer.googleapis.com",
    # Eventarc carries the Firestore change stream to the CDC service, and
    # routes it over Pub/Sub internally.
    "eventarc.googleapis.com",
    "pubsub.googleapis.com"
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


