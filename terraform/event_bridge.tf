# ==============================================================================
# Redwood Retail: Real-Time Event Bridge Daemon on Cloud Run
# Maintains a persistent HTTP/2 gRPC Listen watch stream on Firestore
# /customer_sessions and dispatches PENDING events to the Retention Orchestrator
# Reasoning Engine on Google Cloud Agent Runtime.
# ==============================================================================

variable "enable_event_bridge" {
  description = "Whether to deploy the Firestore-to-Agent-Runtime Event Bridge on Cloud Run."
  type        = bool
  default     = false
}

variable "event_bridge_admin_email" {
  description = "Optional admin email granted roles/run.invoker on the Event Bridge Cloud Run service."
  type        = string
  default     = ""
}

variable "event_bridge_image_tag" {
  description = "Image tag for the event bridge container."
  type        = string
  default     = "latest"
}

variable "event_bridge_min_instances" {
  description = "Minimum instances for the event bridge daemon (must be >= 1 for persistent gRPC listen stream)."
  type        = number
  default     = 1
}

variable "event_bridge_max_instances" {
  description = "Maximum instances for the event bridge daemon."
  type        = number
  default     = 3
}

variable "orchestrator_resource_name" {
  description = "The Vertex AI Agent Runtime resource name of the Retention Orchestrator Reasoning Engine."
  type        = string
  default     = "projects/1097559589092/locations/europe-west4/reasoningEngines/8917030505170337792"
}

locals {
  event_bridge_image = "${var.region}-docker.pkg.dev/${var.project_id}/pipeline-images/event-bridge:${var.event_bridge_image_tag}"
}

resource "google_cloud_run_v2_service" "event_bridge" {
  count    = var.enable_event_bridge ? 1 : 0
  project  = var.project_id
  name     = "redwood-event-bridge"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    service_account       = google_service_account.pipeline_sa.email

    scaling {
      min_instance_count = var.event_bridge_min_instances
      max_instance_count = var.event_bridge_max_instances
    }

    containers {
      image = local.event_bridge_image

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }
        cpu_idle = false # Dedicated CPU (no throttling) to guarantee persistent gRPC stream stability
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "FIRESTORE_DATABASE_ID"
        value = var.firestore_database_id
      }
      env {
        name  = "ORCHESTRATOR_RESOURCE_NAME"
        value = var.enable_agent_engine && length(google_vertex_ai_reasoning_engine.retention_orchestrator) > 0 ? "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.retention_orchestrator[0].name}" : var.orchestrator_resource_name
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
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
    component = "event_bridge"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.sa_firestore_owner
  ]
}

# Allow service account and admin invoker permissions
resource "google_cloud_run_v2_service_iam_member" "event_bridge_invoker" {
  count    = var.enable_event_bridge ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.event_bridge[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

resource "google_cloud_run_v2_service_iam_member" "event_bridge_admin_invoker" {
  count    = var.enable_event_bridge && var.event_bridge_admin_email != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.event_bridge[0].name
  role     = "roles/run.invoker"
  member   = "user:${var.event_bridge_admin_email}"
}
