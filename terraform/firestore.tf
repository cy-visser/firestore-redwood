resource "terraform_data" "firestore_database" {
  triggers_replace = [
    var.project_id,
    var.region,
    var.firestore_database_id,
    var.firestore_edition,
    var.enable_pitr
  ]

  provisioner "local-exec" {
    command = "bash ${path.module}/scripts/manage_firestore_database.sh create '${var.project_id}' '${var.firestore_database_id}' '${var.region}' '${lower(var.firestore_edition)}' '${var.enable_pitr}'"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "bash ${path.module}/scripts/manage_firestore_database.sh delete '${self.triggers_replace[0]}' '${self.triggers_replace[2]}' '${self.triggers_replace[1]}'"
  }

  depends_on = [
    google_project_service.services["firestore.googleapis.com"]
  ]
}
