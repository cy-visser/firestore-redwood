# Vertex AI Agent Engine (Reasoning Engine) Deployment for Redwood Retail
# Discrete A2A Multi-Agent Mesh on Google Cloud Agent Runtime

locals {
  class_methods = jsonencode([
    { name = "set_up", api_mode = "" },
    { name = "query", api_mode = "" },
    { name = "handle_task", api_mode = "" },
    { name = "get_agent_card", api_mode = "" }
  ])

  cooldown_agent_card = jsonencode({
    name             = "Cooldown & Policy Agent"
    description      = "Enforces 7-day offer cooldowns, active offer deduplication, and anti-spam frequency capping."
    protocol_version = "0.3.0"
    capabilities     = ["cooldown-policy", "active-offer-dedup", "anti-spam-frequency-capping"]
    skills = [
      {
        id          = "check_cooldown_eligibility"
        name        = "Check Cooldown & Offer Eligibility"
        description = "Inspects Firestore for active offers and evaluates 7-day cooldown compliance."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  churn_agent_card = jsonencode({
    name             = "Churn Intelligence Agent"
    description      = "Predicts customer churn propensity via BigQuery ML models, fast-path Firestore cache, and 5-pillar heuristic cold-start evaluation."
    protocol_version = "0.3.0"
    capabilities     = ["churn-propensity-prediction", "bigquery-ml", "heuristic-fallback"]
    skills = [
      {
        id          = "evaluate_churn_propensity"
        name        = "Evaluate Customer Churn Propensity"
        description = "Computes calibrated churn probability and risk tier from cache, BQML, or 5-pillar heuristic."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  friction_agent_card = jsonencode({
    name             = "Customer Friction Agent"
    description      = "Analyzes customer relationship context, loyalty tier, spend history, and operational friction events."
    protocol_version = "0.3.0"
    capabilities     = ["friction-analysis", "customer-profiling", "discount-capping"]
    skills = [
      {
        id          = "analyze_friction_and_profile"
        name        = "Analyze Friction and Customer Profile"
        description = "Retrieves customer profile, sentiment score, 90-day spend, and detects acute operational grievances."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  synthesis_agent_card = jsonencode({
    name             = "Offer Synthesis Agent"
    description      = "Synthesizes personalized customer loyalty vouchers using Vertex AI Gemini with deterministic rule fallback."
    protocol_version = "0.3.0"
    capabilities     = ["offer-synthesis", "gemini-generative-ai", "deterministic-guardrails"]
    skills = [
      {
        id          = "synthesize_retention_offer"
        name        = "Synthesize Personalized Retention Offer"
        description = "Composes compelling discount copy, promo codes, and grievance apologies tailored to customer churn context."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  fulfillment_agent_card = jsonencode({
    name             = "Offer Fulfillment Agent"
    description      = "Transactionally commits validated retention vouchers to Cloud Firestore and updates session processing state."
    protocol_version = "0.3.0"
    capabilities     = ["voucher-fulfillment", "firestore-transactions", "audit-ttl-management"]
    skills = [
      {
        id          = "fulfill_loyalty_voucher"
        name        = "Fulfill and Persist Loyalty Voucher"
        description = "Validates offer payload, calculates TTL and cooldown timestamps, writes to /loyalty_offers, and updates session."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  orchestrator_agent_card = jsonencode({
    name             = "Retention Orchestrator Agent"
    description      = "Coordinates multi-agent retention workflows via dynamic A2A discovery and parallel task delegation on Agent Runtime."
    protocol_version = "0.3.0"
    capabilities     = [
      "autonomous-retention-orchestration",
      "dynamic-a2a-discovery",
      "parallel-fanout-delegation",
      "event-augmented-hybrid-scoring"
    ]
    skills = [
      {
        id          = "orchestrate_retention_flow"
        name        = "Orchestrate Customer Retention Flow"
        description = "Executes end-to-end A2A evaluation: checks cooldown, queries churn and friction in parallel, synthesizes voucher copy, and fulfills offer."
      }
    ]
    endpoints = {
      agent_card = "/.well-known/agent-card.json"
      tasks      = "/a2a/v1/tasks"
    }
  })

  # Default alias for backwards compatibility
  agent_card = local.orchestrator_agent_card

  agent_image_uri = "${var.region}-docker.pkg.dev/${var.project_id}/${var.enable_artifact_registry ? google_artifact_registry_repository.pipeline_repo[0].repository_id : "pipeline-images"}/loyalty-agent-runtime:${var.agent_image_tag}"
}

# 1. Standalone Cooldown & Policy Agent
resource "google_vertex_ai_reasoning_engine" "cooldown_agent" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-cooldown-agent"
  description  = "Autonomous Cooldown and Policy Enforcement Agent for Redwood Retail on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "cooldown_agent"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.cooldown_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "cooldown"
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
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user
  ]
}

# 2. Standalone Churn Intelligence Agent
resource "google_vertex_ai_reasoning_engine" "churn_agent" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-churn-agent"
  description  = "Autonomous Churn Intelligence Agent for Redwood Retail on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "churn_agent"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.churn_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "churn"
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
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user
  ]
}

# 3. Standalone Customer Friction Agent
resource "google_vertex_ai_reasoning_engine" "friction_agent" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-friction-agent"
  description  = "Autonomous Customer Friction and Profile Agent for Redwood Retail on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "friction_agent"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.friction_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "friction"
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
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user
  ]
}

# 4. Standalone Offer Synthesis Agent
resource "google_vertex_ai_reasoning_engine" "synthesis_agent" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-synthesis-agent"
  description  = "Autonomous Personalized Offer Synthesis Agent for Redwood Retail on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "synthesis_agent"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.synthesis_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "synthesis"
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "REASONING_MODEL"
        value = var.reasoning_model
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user
  ]
}

# 5. Standalone Offer Fulfillment Agent
resource "google_vertex_ai_reasoning_engine" "fulfillment_agent" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-fulfillment-agent"
  description  = "Autonomous Offer Fulfillment and Persistence Agent for Redwood Retail on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "fulfillment_agent"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.fulfillment_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "fulfillment"
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
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user
  ]
}

# 6. Retention Orchestrator Agent (Discovers and delegates to 1-5 via A2A protocol)
resource "google_vertex_ai_reasoning_engine" "retention_orchestrator" {
  count        = var.enable_agent_engine ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  region       = var.region
  display_name = "redwood-retention-orchestrator"
  description  = "Autonomous Retention Orchestrator delegating to discrete domain agents on Agent Runtime"

  labels = {
    env       = "demo"
    use_case  = "churn_shield"
    component = "retention_orchestrator"
    protocol  = "a2a"
  }

  spec {
    agent_framework = "google-adk"
    agent_card      = local.orchestrator_agent_card
    class_methods   = local.class_methods
    service_account = google_service_account.pipeline_sa.email

    container_spec {
      image_uri = local.agent_image_uri
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
        name  = "AGENT_ROLE"
        value = "orchestrator"
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
        name  = "COOLDOWN_AGENT_URL"
        value = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.cooldown_agent[0].name}"
      }
      env {
        name  = "CHURN_AGENT_URL"
        value = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.churn_agent[0].name}"
      }
      env {
        name  = "FRICTION_AGENT_URL"
        value = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.friction_agent[0].name}"
      }
      env {
        name  = "SYNTHESIS_AGENT_URL"
        value = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.synthesis_agent[0].name}"
      }
      env {
        name  = "FULFILLMENT_AGENT_URL"
        value = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/${google_vertex_ai_reasoning_engine.fulfillment_agent[0].name}"
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name  = "CONTAINER_REVISION"
        value = "2026-09-08-0205"
      }
    }
  }

  depends_on = [
    google_project_service.services["aiplatform.googleapis.com"],
    google_project_iam_member.sa_aiplatform_user,
    google_project_iam_member.re_service_agent_ar_reader,
    google_project_iam_member.re_dedicated_service_agent_ar_reader,
    google_service_account_iam_member.agent_engine_sa_user,
    google_service_account_iam_member.re_dedicated_agent_engine_sa_user,
    google_vertex_ai_reasoning_engine.cooldown_agent,
    google_vertex_ai_reasoning_engine.churn_agent,
    google_vertex_ai_reasoning_engine.friction_agent,
    google_vertex_ai_reasoning_engine.synthesis_agent,
    google_vertex_ai_reasoning_engine.fulfillment_agent
  ]
}
