locals {
  required_services = [
    "firestore.googleapis.com",
    "dataflow.googleapis.com",
    "compute.googleapis.com",
    "bigquery.googleapis.com",
    "bigquerystorage.googleapis.com",
    "bigquerydatatransfer.googleapis.com",
    "storage.googleapis.com",
    "storage-component.googleapis.com",
    "iam.googleapis.com",
    "serviceusage.googleapis.com"
  ]
}

resource "google_project_service" "services" {
  for_each = toset(local.required_services)
  project  = var.project_id
  service  = each.key

  disable_on_destroy         = false
  disable_dependent_services = false
}
