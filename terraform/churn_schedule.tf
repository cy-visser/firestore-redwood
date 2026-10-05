# ==============================================================================
# Daily churn recalculation.
#
# The on-demand path now goes through the redwood-churn Cloud Run function
# (churn_service.tf), which the Redwood Console calls when a presenter presses
# Recalculate. This schedule deliberately does not.
#
# The churn pipeline is a self-contained multi-statement SQL script, so running
# it on a timer needs nothing but BigQuery. Routing the daily run through the
# function as well would add a Cloud Scheduler job, an invoker identity and a
# cold start to accomplish exactly what the Data Transfer Service already does,
# and it would make a nightly refresh depend on a container being deployed.
#
# What the two paths do share is the SQL. The query text below is the same file
# churn_service/Dockerfile bakes into the function image, and both render the
# same placeholders, so the scheduled run and the on-demand run cannot diverge:
# there is one copy of the pipeline and it is this one. Terraform's templatefile
# uses the same ${...} syntax the renderer already used, which is why no
# translation step is needed here.
# ==============================================================================

variable "enable_churn_schedule" {
  description = "Create the daily scheduled query that retrains the churn model and refreshes customer_churn_risk."
  type        = bool
  default     = true
}

variable "churn_schedule" {
  description = "When to re-run the churn pipeline, in BigQuery Data Transfer schedule syntax. Times are UTC."
  type        = string
  default     = "every day 03:00"
}

# The Data Transfer Service runs the query as pipeline_sa rather than as the
# person who applied the Terraform, so the schedule keeps working after that
# person's credentials expire or they leave. Handing it a service account
# requires the transfer service's own agent to be able to mint tokens for that
# account, which is the grant below.
resource "google_project_service_identity" "bigquerydatatransfer" {
  provider = google-beta
  project  = var.project_id
  service  = "bigquerydatatransfer.googleapis.com"

  depends_on = [
    google_project_service.services["bigquerydatatransfer.googleapis.com"]
  ]
}

resource "google_service_account_iam_member" "datatransfer_token_creator" {
  count = var.enable_churn_schedule ? 1 : 0

  service_account_id = google_service_account.pipeline_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.bigquerydatatransfer.email}"
}

resource "google_bigquery_data_transfer_config" "churn_daily" {
  count = var.enable_churn_schedule ? 1 : 0

  project      = var.project_id
  display_name = "Redwood Retail daily churn recalculation"
  location     = google_bigquery_dataset.redwood_retail.location

  data_source_id = "scheduled_query"
  schedule       = var.churn_schedule

  # Run as the pipeline account, not as the applying user.
  service_account_name = google_service_account.pipeline_sa.email

  params = {
    query = templatefile("${path.module}/../bigquery_churn_sentiment_analysis.sql", {
      GCP_PROJECT_ID           = var.project_id
      BIGQUERY_DATASET         = google_bigquery_dataset.redwood_retail.dataset_id
      BIGQUERY_ORDERS_TABLE    = "${var.firestore_collection}_current"
      BIGQUERY_HISTORICAL_VIEW = "customer_historical_data"
      BIGQUERY_CHURN_MODEL     = "customer_churn_model"
    })
  }

  # The script creates its own views, model and tables, so there is no single
  # destination table to declare. Setting one would make the service try to
  # write the script's final result into it, which a MERGE does not produce.

  depends_on = [
    google_project_service.services["bigquerydatatransfer.googleapis.com"],
    google_service_account_iam_member.datatransfer_token_creator,
    google_project_iam_member.sa_bigquery_editor,
    google_project_iam_member.sa_bigquery_job_user,
  ]
}

output "churn_schedule_name" {
  description = "Resource name of the daily churn scheduled query, empty when disabled."
  value       = var.enable_churn_schedule ? google_bigquery_data_transfer_config.churn_daily[0].name : ""
}
