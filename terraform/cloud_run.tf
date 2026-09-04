# ==============================================================================
# Redwood Retail: Scheduled Cloud Run Reverse-ETL Pipeline
# Executes sync_churn_to_firestore.py every 24 hours via Cloud Scheduler.
# ==============================================================================

# Artifact Registry repository for container images
resource "google_artifact_registry_repository" "pipeline_repo" {
  count         = var.enable_churn_sync_job ? 1 : 0
  project       = var.project_id
  location      = var.region
  repository_id = "pipeline-images"
  description   = "Docker repository for Redwood Retail pipeline components"
  format        = "DOCKER"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_project_service.services["artifactregistry.googleapis.com"]
  ]
}

# Cloud Run Job for Reverse-ETL batch synchronization
resource "google_cloud_run_v2_job" "churn_sync_job" {
  count    = var.enable_churn_sync_job ? 1 : 0
  project  = var.project_id
  name     = "churn-sync-job"
  location = var.region

  template {
    template {
      max_retries     = 3
      timeout         = "600s"
      service_account = google_service_account.pipeline_sa.email

      containers {
        image = var.churn_sync_image != "" ? var.churn_sync_image : "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.pipeline_repo[0].repository_id}/churn-sync:latest"

        resources {
          limits = {
            cpu    = "1000m"
            memory = "1Gi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "FIRESTORE_DATABASE_ID"
          value = var.firestore_database_id
        }
        env {
          name  = "BIGQUERY_DATASET"
          value = var.bigquery_dataset_id
        }
        env {
          name  = "BIGQUERY_PREDICTIONS_TABLE"
          value = var.bigquery_predictions_table_id
        }
      }
    }
  }

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_iam_member.sa_run_developer,
    google_project_iam_member.sa_firestore_owner,
    google_project_iam_member.sa_bigquery_editor
  ]
}

# Cloud Scheduler job to trigger the Cloud Run Job every 24 hours
resource "google_cloud_scheduler_job" "churn_sync_scheduler" {
  count       = var.enable_churn_sync_job ? 1 : 0
  project     = var.project_id
  name        = "daily-churn-sync-scheduler"
  description = "Triggers the Cloud Run Reverse-ETL sync job every 24 hours"
  region      = var.region
  schedule    = var.churn_sync_schedule
  time_zone   = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.churn_sync_job[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.pipeline_sa.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.services["cloudscheduler.googleapis.com"],
    google_project_iam_member.sa_run_invoker
  ]
}
