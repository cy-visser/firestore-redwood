resource "google_firestore_database" "database" {
  project                             = var.project_id
  name                                = var.firestore_database_id
  location_id                         = var.region
  type                                = "FIRESTORE_NATIVE"
  database_edition                    = upper(var.firestore_edition)
  firestore_data_access_mode          = upper(var.firestore_edition) == "ENTERPRISE" ? "DATA_ACCESS_MODE_ENABLED" : null
  realtime_updates_mode               = upper(var.firestore_edition) == "ENTERPRISE" ? "REALTIME_UPDATES_MODE_ENABLED" : null
  mongodb_compatible_data_access_mode = upper(var.firestore_edition) == "ENTERPRISE" ? "DATA_ACCESS_MODE_DISABLED" : null
  point_in_time_recovery_enablement   = var.enable_pitr ? "POINT_IN_TIME_RECOVERY_ENABLED" : "POINT_IN_TIME_RECOVERY_DISABLED"
  delete_protection_state             = "DELETE_PROTECTION_DISABLED"
  deletion_policy                     = "DELETE"

  depends_on = [
    google_project_service.services["firestore.googleapis.com"]
  ]
}
