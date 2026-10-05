# ==============================================================================
# Redwood Retail: the mobile storefront and the Redwood Console on Cloud Run.
#
# Replaces start_mobile_app.sh as the way the demo is served. That script ran
# uvicorn and a Vite dev server on the presenter's laptop, which meant the
# "mobile app" was only reachable from the machine running it.
#
# The service is IAM-only. A browser hitting the run.app URL directly gets a
# 403, because Cloud Run has no interactive sign-in without IAP; the way in is
# scripts/run_proxy.py, a local tunnel that signs each request with an ID token
# minted by impersonating the pipeline service account. deploy.sh starts it as
# its last step.
# ==============================================================================

variable "app_image_tag" {
  description = "Container image tag for the app. Only used when app_image_digest is empty."
  type        = string
  default     = "latest"
}

variable "app_image_digest" {
  description = "Image digest (sha256:...) to deploy. Preferred over the tag, so a rebuild is an actual change to Terraform rather than an empty plan."
  type        = string
  default     = ""
}

variable "app_invoker_members" {
  description = <<-EOT
    Identities allowed to invoke redwood-app and redwood-churn, as IAM member
    strings ("user:you@example.com").

    The services are IAM-only -- there is no allUsers binding -- so without an
    entry here nobody can open the app, not even through the local tunnel.
    deploy.sh populates this with the account running it.

    Membership grants two things together: run.invoker on the services, and
    serviceAccountTokenCreator on the pipeline account, which is the identity
    the tunnel actually presents.
  EOT
  type        = list(string)
  default     = []
}

variable "app_enable_demo_controls" {
  description = "Whether the console's Reset Demo and Recalculate Churn buttons are active. On by default: a demo that cannot be reset between runs is not much of a demo, and the service is IAM-gated anyway."
  type        = bool
  default     = true
}

locals {
  app_image_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}/redwood-app"

  app_image = var.app_image_digest != "" ? "${local.app_image_base}@${var.app_image_digest}" : "${local.app_image_base}:${var.app_image_tag}"
}

resource "google_cloud_run_v2_service" "app" {
  project  = var.project_id
  name     = "redwood-app"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.pipeline_sa.email

    # Pinned to exactly one instance, which is a correctness constraint rather
    # than a capacity one. console_service keeps the SSE Broadcaster, the
    # browser-clock telemetry and the recent-order buffer in process memory, so
    # a console served by a second instance would never see an order placed
    # against the first. There is one presenter.
    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    # Plenty for one presenter and a handful of open SSE streams, and it has to
    # be well above 1: each console tab holds a request open indefinitely, so a
    # low limit would lock out the very next page load.
    max_instance_request_concurrency = 80

    # An SSE stream is a request that never ends on purpose. 3600s is the
    # ceiling Cloud Run allows, and it is what bounds a console left open over
    # a lunch break; the browser reconnects afterwards.
    timeout = "3600s"

    containers {
      image = local.app_image

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1Gi"
        }

        # Firestore on_snapshot callbacks arrive on background threads. Under
        # Cloud Run's default request-scoped CPU those threads are throttled to
        # near nothing between requests, and the console would sit silent until
        # some other HTTP call happened to wake the instance. This is what
        # makes the live panels live.
        cpu_idle = false

        startup_cpu_boost = true
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
        name  = "BIGQUERY_DATASET"
        value = google_bigquery_dataset.redwood_retail.dataset_id
      }
      env {
        name  = "BIGQUERY_ORDERS_TABLE"
        value = "${var.firestore_collection}_current"
      }
      env {
        name  = "BIGQUERY_PREDICTIONS_TABLE"
        value = "customer_churn_risk"
      }
      env {
        name  = "ENABLE_DEMO_CONTROLS"
        value = var.app_enable_demo_controls ? "1" : "0"
      }
      # Where Recalculate Churn and the reset's re-score step send their work.
      # Empty would leave the console explaining itself instead of failing
      # obscurely, but it is never empty here.
      env {
        name  = "CHURN_FUNCTION_URL"
        value = google_cloud_run_v2_service.churn.uri
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      startup_probe {
        http_get {
          path = "/api/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        # Generous: the health check opens a Firestore client and runs a
        # one-document query, and the first one pays for the connection.
        failure_threshold = 12
        timeout_seconds   = 5
      }
    }
  }

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "app"
  }

  depends_on = [
    google_project_service.services["run.googleapis.com"],
    google_project_iam_member.sa_firestore_owner,
    google_project_iam_member.sa_bigquery_job_user,
    google_firestore_database.database,
  ]
}

# ------------------------------------------------------------------------------
# Who may call what.
# ------------------------------------------------------------------------------

# The app calls the churn function as itself, using an ID token minted from the
# metadata server for the function's URL as audience.
resource "google_cloud_run_v2_service_iam_member" "churn_invoker_app" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.churn.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Operators reach the app through scripts/run_proxy.py, which signs each
# request with an ID token minted by impersonating this account -- so the
# account itself needs to be an invoker, not just the humans.
#
# The humans are granted below as well. Both matter: this one is the identity
# on the wire, theirs is the permission to assume it.
resource "google_cloud_run_v2_service_iam_member" "app_invoker_pipeline_sa" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

# Operators, for reaching the app through the local tunnel.
resource "google_cloud_run_v2_service_iam_member" "app_invokers" {
  for_each = toset(var.app_invoker_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = each.value
}

# Operators, for the deploy-time churn run and for anything hitting the
# function directly with curl.
resource "google_cloud_run_v2_service_iam_member" "churn_invokers" {
  for_each = toset(var.app_invoker_members)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.churn.name
  role     = "roles/run.invoker"
  member   = each.value
}

# Everything off Cloud Run mints its ID tokens by impersonating the pipeline
# account, because a user credential cannot issue one for a Cloud Run audience
# -- the audience of a user ID token is fixed to the OAuth client that issued
# it. That covers the local tunnel, a locally-run backend, and deploy.sh's own
# call to the churn function. Without this grant the deployed app works while
# every local path 403s, which is a confusing pair of symptoms to hold at once.
resource "google_service_account_iam_member" "app_invoker_token_creator" {
  for_each = toset(var.app_invoker_members)

  service_account_id = google_service_account.pipeline_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

output "app_service_url" {
  description = "Base URL of the Redwood app. IAM-only: a browser gets 403 without the tunnel."
  value       = google_cloud_run_v2_service.app.uri
}

output "app_proxy_command" {
  description = "The command that makes the app reachable from a browser."
  # Deliberately not `gcloud run services proxy`. That cannot authenticate to
  # this service: with user credentials it presents a token whose audience is
  # the gcloud OAuth client, which Cloud Run rejects, and with
  # --impersonate-service-account the proxy binary refuses the credential type
  # outright. scripts/run_proxy.py does the same job with a working token.
  value = "python3 scripts/run_proxy.py --url ${google_cloud_run_v2_service.app.uri} --port 8080"
}
