# Vertex AI Agent Engine (Reasoning Engine) Deployment for Redwood Retail
# Pure Agent Runtime deployment with A2A multi-agent capabilities

locals {
  agent_card = jsonencode({
    name             = var.agent_engine_display_name
    description      = var.agent_engine_description
    protocol_version = "0.3.0"
    capabilities     = [
      "autonomous-retention-loop",
      "multi-agent-orchestration",
      "session-to-churn-correlation",
      "personalized-offer-generation",
      "agent-to-agent-task-delegation"
    ]
    skills = [
      {
        id          = "process-customer-session"
        name        = "Process Customer Session"
        description = "Analyzes real-time customer session, evaluates churn risk via BigQuery ML, and generates retention offers"
      },
      {
        id          = "query-retention-state"
        name        = "Query Retention State"
        description = "Queries real-time session processing state and generated offers in Firestore"
      },
      {
        id          = "delegate-agent-task"
        name        = "Delegate Agent Task"
        description = "Delegates subtasks to specialized churn predictor or offer generation subagents"
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/tasks"
    }
  })

  class_methods = jsonencode([
    { name = "set_up", api_mode = "" },
    { name = "query", api_mode = "" },
    { name = "handle_task", api_mode = "" },
    { name = "get_agent_card", api_mode = "" }
  ])
}

resource "google_vertex_ai_reasoning_engine" "retention_agent_engine" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = var.agent_engine_display_name
  description  = var.agent_engine_description

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "agent_engine"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = "${var.region}-docker.pkg.dev/${var.project_id}/${var.enable_artifact_registry ? google_artifact_registry_repository.pipeline_repo[0].repository_id : "pipeline-images"}/loyalty-agent-runtime:${var.agent_image_tag}"
      port      = 8080
    }

    deployment_spec {
      min_instances = var.agent_min_instances
      max_instances = var.agent_max_instances

      resource_limits = {
        cpu    = var.agent_cpu
        memory = var.agent_memory
      }

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
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
      env {
        name  = "REASONING_MODEL"
        value = var.reasoning_model
      }
      env {
        name  = "PORT"
        value = "8080"
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user
  ]
}
