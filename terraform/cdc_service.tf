# ==============================================================================
# Redwood Retail: Firestore -> BigQuery change data capture.
#
# Replaces the Dataflow streaming job. That job could not actually tail
# Firestore: Beam's Python SDK has no Firestore connector, there is no managed
# Firestore-to-BigQuery template, Datastream does not support Firestore as a
# source, and the MongoDB change streams that would have worked are unreachable
# because Firestore Enterprise is either Native mode or MongoDB-compatible and
# never both. Eventarc is the only real change stream available here.
#
# Delivery is at-least-once and unordered, which the service handles: the
# ledger is append-only and the mirror tables are keyed and sequenced.
# ==============================================================================

variable "firestore_customers_collection" {
  description = "Firestore collection holding customer profile documents, replicated to BigQuery alongside orders."
  type        = string
  default     = "customers"
}

variable "cdc_image_tag" {
  description = "Container image tag for the CDC service. Only used when cdc_image_digest is empty."
  type        = string
  default     = "latest"
}

variable "cdc_image_digest" {
  description = <<-EOT
    Image digest (sha256:...) to deploy. Strongly preferred over the tag.

    A mutable tag is the same string to Terraform before and after a rebuild,
    so the plan is empty and the new image never rolls out: the service keeps
    serving whatever digest `latest` happened to point at when the revision was
    first created. deploy.sh captures the digest from the build and passes it
    here so that rebuilding is actually a change.
  EOT
  type        = string
  default     = ""
}

variable "cdc_min_instances" {
  description = "Minimum CDC service instances. Held at 1 so the warm BigQuery append stream survives between events and the demo does not pay a cold start on the first write."
  type        = number
  default     = 1
}

variable "cdc_max_instances" {
  description = "Maximum CDC service instances."
  type        = number
  default     = 10
}

locals {
  cdc_image_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}/redwood-cdc"

  cdc_image = var.cdc_image_digest != "" ? "${local.cdc_image_base}@${var.cdc_image_digest}" : "${local.cdc_image_base}:${var.cdc_image_tag}"

  # Each collection needs its own trigger: Eventarc matches on a document path
  # pattern, and there is no way to express "any of these two collections" in
  # a single trigger.
  cdc_collections = {
    orders    = var.firestore_collection
    customers = var.firestore_customers_collection
  }
}

# ------------------------------------------------------------------------------
# Service agents. Both must exist before Eventarc will accept a Firestore
# trigger, and creating them is not implied by enabling the APIs.
# ------------------------------------------------------------------------------
resource "google_project_service_identity" "eventarc_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "eventarc.googleapis.com"

  depends_on = [google_project_service.services["eventarc.googleapis.com"]]
}

resource "google_project_service_identity" "firestore_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "firestore.googleapis.com"

  depends_on = [google_project_service.services["firestore.googleapis.com"]]
}

# The Eventarc service agent needs this to publish into the trigger's
# transport. Granting it is a documented prerequisite, and the failure mode
# without it is a misleading "Permission denied while using the Eventarc
# Service Agent" on trigger creation.
resource "google_project_iam_member" "eventarc_service_agent" {
  project = var.project_id
  role    = "roles/eventarc.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.eventarc_sa.email}"
}

# ------------------------------------------------------------------------------
# Permissions for the service account the CDC service and triggers run as.
# ------------------------------------------------------------------------------

# Storage Write API appends, including the CDC UPSERT/DELETE path, need
# bigquery.tables.updateData. That comes with roles/bigquery.dataEditor, which
# iam.tf already grants to this service account as sa_bigquery_editor, so there
# is no extra binding here.

# Required for the trigger to deliver events at all.
resource "google_project_iam_member" "sa_eventarc_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# ------------------------------------------------------------------------------
# The CDC service itself.
# ------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "cdc" {
  project  = var.project_id
  name     = "redwood-cdc"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.pipeline_sa.email

    scaling {
      min_instance_count = var.cdc_min_instances
      max_instance_count = var.cdc_max_instances
    }

    # Eventarc redelivers on 5xx, so a request that outlives this is retried
    # rather than lost.
    timeout = "120s"

    containers {
      image = local.cdc_image

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
        name  = "FIRESTORE_COLLECTION"
        value = var.firestore_collection
      }
      env {
        name  = "FIRESTORE_CUSTOMERS_COLLECTION"
        value = var.firestore_customers_collection
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = google_bigquery_dataset.redwood_retail.dataset_id
      }
      env {
        name  = "BIGQUERY_CDC_TABLE"
        value = google_bigquery_table.orders_cdc.table_id
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
    component = "cdc"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_iam_member.sa_bigquery_editor,
    google_project_iam_member.sa_bigquery_job_user,
    google_bigquery_table.orders_cdc,
  ]
}

# Eventarc invokes the service as the trigger's service account, so that
# identity needs run.invoker on this specific service.
resource "google_cloud_run_v2_service_iam_member" "cdc_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.cdc.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# ------------------------------------------------------------------------------
# Triggers. Named (non-default) Firestore databases are only supported as an
# Eventarc source for Cloud Run and 2nd-gen Functions destinations.
# ------------------------------------------------------------------------------
resource "google_eventarc_trigger" "firestore_write" {
  for_each = local.cdc_collections

  project  = var.project_id
  name     = "redwood-cdc-${each.key}"
  location = var.region

  # `written` covers create, update and delete in one subscription, so a
  # document removal reaches BigQuery as a DELETE instead of silently leaving
  # a stale row behind, which is what the Dataflow job did.
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.firestore.document.v1.written"
  }

  matching_criteria {
    attribute = "database"
    value     = var.firestore_database_id
  }

  # PATH_PATTERN scopes the trigger to one collection; '*' matches a single
  # path segment, so this is every document directly under the collection.
  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = "${each.value}/*"
  }

  # Firestore sources accept only protobuf here. Setting application/json is
  # rejected at creation time with a trigger.event_data_content_type field
  # violation, so the service decodes the binary DocumentEventData message.
  event_data_content_type = "application/protobuf"

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.cdc.name
      region  = var.region
      path    = "/"
    }
  }

  service_account = google_service_account.pipeline_sa.email

  labels = {
    env       = "demo"
    component = "cdc"
  }

  depends_on = [
    google_project_service.services["eventarc.googleapis.com"],
    google_project_service_identity.eventarc_sa,
    google_project_service_identity.firestore_sa,
    google_project_iam_member.eventarc_service_agent,
    google_project_iam_member.sa_eventarc_receiver,
    google_cloud_run_v2_service_iam_member.cdc_invoker,
    terraform_data.firestore_database,
  ]
}

output "cdc_service_url" {
  description = "Base URL of the CDC service. Reconcile collections seeded before the triggers existed with cdc_service/backfill.py."
  value       = google_cloud_run_v2_service.cdc.uri
}

output "cdc_triggers" {
  description = "Eventarc triggers replicating Firestore collections into BigQuery."
  value       = { for k, t in google_eventarc_trigger.firestore_write : k => t.name }
}
