# ==============================================================================
# Redwood Retail: Firestore session write -> loyalty agent.
#
# Replaces terraform/agent_engine.tf, which declared six reasoning engines for
# the A2A mesh, and terraform/event_bridge.tf, which was the disabled and
# stale-by-construction precursor to this file. There is now one agent.
#
# Agent Engine cannot be an Eventarc destination, so a Cloud Run service sits
# between the two and does nothing but translate one into the other. It is the
# same shape as the CDC service, which already established that Eventarc
# delivery works against this named Enterprise Native database.
#
# The agent itself is not declared here. It is deployed from
# scripts/deploy_agent_engine.py, because the object-based deployment path
# takes the LoyaltyAgentEngine class directly and needs no container. That
# leaves the question of how the bridge finds it, and the answer is by display
# name, not by id: event_bridge.tf used to carry a literal reasoning engine id
# as a Terraform default, which pointed at a different project's resource and
# was wrong the moment the agent was redeployed.
# ==============================================================================

variable "enable_agent_bridge" {
  description = "Create the Cloud Run bridge and the Firestore session trigger that invokes the loyalty agent."
  type        = bool
  default     = true
}

variable "firestore_sessions_collection" {
  description = "Firestore collection the mobile client writes login sessions into."
  type        = string
  default     = "customer_sessions"
}

variable "agent_display_name" {
  description = "Display name of the Agent Engine deployment. The bridge resolves the agent by this name, so it must match scripts/deploy_agent_engine.py."
  type        = string
  default     = "redwood-loyalty-agent"
}

variable "agent_bridge_image_tag" {
  description = "Container image tag for the bridge. Only used when agent_bridge_image_digest is empty."
  type        = string
  default     = "latest"
}

variable "agent_bridge_image_digest" {
  description = <<-EOT
    Image digest (sha256:...) to deploy. Strongly preferred over the tag.

    A mutable tag is the same string to Terraform before and after a rebuild,
    so the plan comes out empty and the new image never rolls out. This bit us
    on the CDC service: it served a stale digest while `latest` had already
    moved, and every event failed to parse against the old code path.
  EOT
  type        = string
  default     = ""
}

variable "agent_bridge_min_instances" {
  description = "Minimum bridge instances. Held at 1 so the resolved agent handle stays warm and a demo login does not wait on a cold start plus an engine lookup."
  type        = number
  default     = 1
}

variable "agent_bridge_max_instances" {
  description = "Maximum bridge instances."
  type        = number
  default     = 5
}

locals {
  agent_bridge_image_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}/redwood-agent-bridge"

  agent_bridge_image = var.agent_bridge_image_digest != "" ? "${local.agent_bridge_image_base}@${var.agent_bridge_image_digest}" : "${local.agent_bridge_image_base}:${var.agent_bridge_image_tag}"
}

# ------------------------------------------------------------------------------
# The bridge service.
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "agent_bridge" {
  count = var.enable_agent_bridge ? 1 : 0

  project  = var.project_id
  name     = "redwood-agent-bridge"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.pipeline_sa.email

    scaling {
      min_instance_count = var.agent_bridge_min_instances
      max_instance_count = var.agent_bridge_max_instances
    }

    # Generous, because the request is spent waiting on the agent and a cold
    # agent has to start before it can answer. Timing out here would return
    # 5xx and make Eventarc redeliver an event the agent is still working on.
    timeout = "300s"

    containers {
      image = local.agent_bridge_image

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }
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
        name  = "FIRESTORE_SESSIONS_COLLECTION"
        value = var.firestore_sessions_collection
      }
      env {
        name  = "AGENT_DISPLAY_NAME"
        value = var.agent_display_name
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
        period_seconds        = 5
        failure_threshold     = 10
        timeout_seconds       = 3
      }
    }
  }

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "agent_bridge"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "agent_bridge_invoker" {
  count = var.enable_agent_bridge ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.agent_bridge[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# ------------------------------------------------------------------------------
# The trigger.
# ------------------------------------------------------------------------------
resource "google_eventarc_trigger" "session_write" {
  count = var.enable_agent_bridge ? 1 : 0

  project  = var.project_id
  name     = "redwood-agent-sessions"
  location = var.region

  # `written` rather than `created`: Firestore publishes one event type for
  # creates, updates and deletes, so there is no create-only subscription to
  # ask for. The bridge decides what to act on, and only acts on a session
  # still marked PENDING, which is what keeps the agent's own write-back from
  # triggering another run.
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.firestore.document.v1.written"
  }

  matching_criteria {
    attribute = "database"
    value     = var.firestore_database_id
  }

  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = "${var.firestore_sessions_collection}/*"
  }

  # Firestore sources accept only protobuf. application/json is rejected at
  # creation time with a trigger.event_data_content_type field violation.
  event_data_content_type = "application/protobuf"

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.agent_bridge[0].name
      region  = var.region
      path    = "/"
    }
  }

  service_account = google_service_account.pipeline_sa.email

  labels = {
    env       = "demo"
    component = "agent_bridge"
  }

  depends_on = [
    google_project_service.services["eventarc.googleapis.com"],
    google_project_service_identity.eventarc_sa,
    google_project_service_identity.firestore_sa,
    google_project_iam_member.eventarc_service_agent,
    google_project_iam_member.sa_eventarc_receiver,
    google_cloud_run_v2_service_iam_member.agent_bridge_invoker,
    google_firestore_database.database,
  ]
}

output "agent_bridge_url" {
  description = "Base URL of the agent bridge, empty when disabled."
  value       = var.enable_agent_bridge ? google_cloud_run_v2_service.agent_bridge[0].uri : ""
}

output "agent_session_trigger" {
  description = "Eventarc trigger invoking the loyalty agent on session writes, empty when disabled."
  value       = var.enable_agent_bridge ? google_eventarc_trigger.session_write[0].name : ""
}
