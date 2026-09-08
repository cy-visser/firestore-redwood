# ==============================================================================
# Firestore Security Rules (Frontend Client Protection)
# ==============================================================================

resource "google_firebaserules_ruleset" "firestore" {
  count   = var.enable_security_rules ? 1 : 0
  project = var.project_id

  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/firestore.rules")
    }
  }

  depends_on = [
    google_project_service.services["firestore.googleapis.com"]
  ]
}

resource "google_firebaserules_release" "firestore" {
  count        = var.enable_security_rules ? 1 : 0
  project      = var.project_id
  name         = "cloud.firestore/${var.firestore_database_id}"
  ruleset_name = "projects/${var.project_id}/rulesets/${google_firebaserules_ruleset.firestore[0].name}"

  lifecycle {
    replace_triggered_by = [
      google_firebaserules_ruleset.firestore[0]
    ]
  }

  depends_on = [
    google_firebaserules_ruleset.firestore,
    terraform_data.firestore_database
  ]
}
