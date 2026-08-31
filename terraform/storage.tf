resource "google_storage_bucket" "redwood_bucket" {
  name                        = "${var.project_id}-${var.gcs_bucket_name_prefix}-${var.region}"
  project                     = var.project_id
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_project_service.services["storage.googleapis.com"]
  ]
}
