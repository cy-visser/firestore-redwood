resource "google_firestore_database" "database" {
  provider = google-beta
  project  = var.project_id
  name     = var.firestore_database_id

  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  database_edition                  = var.firestore_edition
  point_in_time_recovery_enablement = var.enable_pitr ? "POINT_IN_TIME_RECOVERY_ENABLED" : "POINT_IN_TIME_RECOVERY_DISABLED"
  deletion_policy                   = "DELETE"
  delete_protection_state           = "DELETE_PROTECTION_DISABLED"

  depends_on = [
    google_project_service.services["firestore.googleapis.com"]
  ]
}
