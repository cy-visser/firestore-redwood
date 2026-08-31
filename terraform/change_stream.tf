# Firestore MongoDB Change Stream for 'orders' Collection Group
resource "terraform_data" "firestore_change_stream" {
  count = var.enable_change_stream ? 1 : 0

  triggers_replace = [
    var.project_id,
    var.region,
    var.firestore_database_id,
    var.change_stream_name,
    var.change_stream_collection_group,
    var.change_stream_retention,
    google_firestore_database.database.id
  ]

  provisioner "local-exec" {
    command = <<-EOT
      bash ${path.module}/scripts/setup_change_stream.sh \
        --project="${var.project_id}" \
        --database="${var.firestore_database_id}" \
        --stream-id="${var.change_stream_name}" \
        --collection-group="${var.change_stream_collection_group}" \
        --retention="${var.change_stream_retention}"
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      bash ${path.module}/scripts/cleanup_change_stream.sh \
        --project="${self.triggers_replace[0]}" \
        --database="${self.triggers_replace[2]}" \
        --stream-id="${self.triggers_replace[3]}"
    EOT
  }

  depends_on = [
    google_firestore_database.database,
    google_project_service.services["firestore.googleapis.com"]
  ]
}
