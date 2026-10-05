# ==============================================================================
# Redwood Retail: the churn pipeline as a Cloud Run function.
#
# Replaces run_bigquery_analysis.py, which the Redwood Console used to spawn as
# a subprocess on the presenter's workstation. That meant the demo could only
# be driven from the machine that held the repository, a virtual environment
# and Application Default Credentials.
#
# The SQL is unchanged and still lives at the repository root, baked into this
# image by churn_service/Dockerfile. churn_schedule.tf renders the same file
# for the daily run, so there is one copy of the pipeline and two ways to start
# it.
# ==============================================================================

variable "churn_image_tag" {
  description = "Container image tag for the churn function. Only used when churn_image_digest is empty."
  type        = string
  default     = "latest"
}

variable "churn_image_digest" {
  description = <<-EOT
    Image digest (sha256:...) to deploy. Strongly preferred over the tag, for
    the same reason as cdc_image_digest: a mutable tag is the same string to
    Terraform before and after a rebuild, so the plan is empty and the new
    image never rolls out. deploy.sh captures the digest from the build and
    passes it here.
  EOT
  type        = string
  default     = ""
}

variable "churn_timeout_seconds" {
  description = "Request timeout for a churn run. A full pipeline retrains the BQML model, which scales with the seeded dataset; 900s is roughly ten times the observed time at the default seed count."
  type        = number
  default     = 900
}

locals {
  churn_image_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}/redwood-churn"

  churn_image = var.churn_image_digest != "" ? "${local.churn_image_base}@${var.churn_image_digest}" : "${local.churn_image_base}:${var.churn_image_tag}"
}

resource "google_cloud_run_v2_service" "churn" {
  project  = var.project_id
  name     = "redwood-churn"
  location = var.region

  # Gated by IAM, not by the network. Only the app's service account and the
  # operators in app_invoker_members hold run.invoker below; there is no
  # allUsers binding anywhere in this file.
  ingress = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.pipeline_sa.email

    # Scales to zero. This runs when somebody presses a button, and a cold
    # start is invisible next to a pipeline that takes over a minute. The
    # ceiling of 2 exists so a double-clicked button cannot start a third
    # concurrent retrain of the same model.
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    # One run per instance. The pipeline is a serial sequence of BigQuery jobs
    # and interleaving two of them in one process buys nothing but a confusing
    # log.
    max_instance_request_concurrency = 1

    timeout = "${var.churn_timeout_seconds}s"

    containers {
      image = local.churn_image

      resources {
        limits = {
          cpu    = "1000m"
          memory = "512Mi"
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
        name  = "BIGQUERY_DATASET"
        value = google_bigquery_dataset.redwood_retail.dataset_id
      }
      env {
        name  = "BIGQUERY_CDC_TABLE"
        value = google_bigquery_table.orders_cdc.table_id
      }
      # The typed CDC mirror the feature view reads, not the raw ledger.
      env {
        name  = "BIGQUERY_ORDERS_TABLE"
        value = "${var.firestore_collection}_current"
      }
      env {
        name  = "BIGQUERY_HISTORICAL_VIEW"
        value = "customer_historical_data"
      }
      env {
        name  = "BIGQUERY_CHURN_MODEL"
        value = "customer_churn_model"
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
    component = "churn"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_iam_member.sa_bigquery_editor,
    google_project_iam_member.sa_bigquery_job_user,
    # The pipeline reads the mirror tables, so it must not come up before
    # Terraform has created them.
    google_bigquery_table.orders_current,
    google_bigquery_table.customers_current,
  ]
}

output "churn_service_url" {
  description = "Base URL of the churn function. POST {\"mode\": \"full\"|\"rescore\"} with an ID token for this audience."
  value       = google_cloud_run_v2_service.churn.uri
}
