# ==============================================================================
# Redwood Retail: Autonomous Loyalty Agent Cloud Run Daemon
# Runs loyalty_agent/main.py as an always-on background listener for Firestore
# customer login sessions, evaluating churn and issuing retention offers.
# ==============================================================================

# Artifact Registry repository for container images
resource "google_artifact_registry_repository" "pipeline_repo" {
  count         = var.enable_artifact_registry ? 1 : 0
  project       = var.project_id
  location      = var.region
  repository_id = "pipeline-images"
  description   = "Docker repository for Redwood Retail pipeline and agent images"
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

# Cloud Run Service running the persistent Loyalty Agent Daemon
resource "google_cloud_run_v2_service" "loyalty_agent_daemon" {
  count    = var.enable_loyalty_agent_daemon ? 1 : 0
  project  = var.project_id
  name     = "loyalty-agent-daemon"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    service_account       = google_service_account.pipeline_sa.email

    scaling {
      min_instance_count = 1 # Always-on daemon to maintain persistent gRPC listener
      max_instance_count = 3
    }

    containers {
      image = var.loyalty_agent_image != "" ? var.loyalty_agent_image : "${var.region}-docker.pkg.dev/${var.project_id}/pipeline-images/loyalty-agent-daemon:latest"

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }
        cpu_idle = false # Dedicated CPU (no throttling) to keep gRPC stream active
      }

      ports {
        container_port = 8080
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
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = var.bigquery_dataset_id
      }
      env {
        name  = "BIGQUERY_PREDICTIONS_TABLE"
        value = var.bigquery_predictions_table_id
      }
      env {
        name  = "REASONING_MODEL"
        value = var.reasoning_model
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 5
        timeout_seconds       = 3
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        period_seconds    = 15
        failure_threshold = 3
        timeout_seconds   = 3
      }
    }
  }

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    datacloud = "antigravity"
    component = "loyalty_agent"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.sa_firestore_owner,
    google_project_iam_member.sa_run_developer,
    google_project_iam_member.sa_run_invoker
  ]
}

# Allow service account and admin user health checks and invocations (Argolis org-policy compliant)
resource "google_cloud_run_v2_service_iam_member" "loyalty_agent_invoker" {
  count    = var.enable_loyalty_agent_daemon ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.loyalty_agent_daemon[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

resource "google_cloud_run_v2_service_iam_member" "loyalty_agent_admin_invoker" {
  count    = var.enable_loyalty_agent_daemon ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.loyalty_agent_daemon[0].name
  role     = "roles/run.invoker"
  member   = "user:admin@ganeshraja.altostrat.com"
}
