resource "random_id" "project_suffix" {
  byte_length = 3
}

locals {
  computed_project_id = var.project_id != "" ? lower(var.project_id) : lower("${var.project_prefix}-${random_id.project_suffix.hex}")
  clean_folder_id     = var.folder_id != "" ? (startswith(var.folder_id, "folders/") ? var.folder_id : "folders/${var.folder_id}") : null
  clean_org_id        = var.folder_id == "" && var.org_id != "" ? var.org_id : null

  foundational_apis = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com"
  ]
}

resource "google_project" "project" {
  name            = var.project_name
  project_id      = local.computed_project_id
  folder_id       = local.clean_folder_id
  org_id          = local.clean_org_id
  billing_account = var.billing_account_id

  auto_create_network = false
  deletion_policy     = var.deletion_policy
}

# Enable core foundation APIs required for subsequent service provisioning
resource "google_project_service" "foundational_services" {
  for_each = toset(local.foundational_apis)
  project  = google_project.project.project_id
  service  = each.key

  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.project]
}

# Optionally grant Owner role to the specified admin identity (e.g. admin@ganeshraja.altostrat.com)
resource "google_project_iam_member" "primary_owner" {
  count   = var.primary_owner_user_email != "" ? 1 : 0
  project = google_project.project.project_id
  role    = "roles/owner"
  member  = startswith(var.primary_owner_user_email, "user:") || startswith(var.primary_owner_user_email, "serviceAccount:") ? var.primary_owner_user_email : "user:${var.primary_owner_user_email}"

  depends_on = [google_project_service.foundational_services]
}
