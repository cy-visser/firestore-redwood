# ==============================================================================
# Redwood Retail: Artifact Registry Repository
# Stores container images for pipeline and Agent Runtime deployments.
# ==============================================================================

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
