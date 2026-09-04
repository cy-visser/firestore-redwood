output "project_id" {
  description = "The Google Cloud Project ID created by this bootstrap module."
  value       = google_project.project.project_id
}

output "project_number" {
  description = "The Google Cloud Project Number."
  value       = google_project.project.number
}

output "project_name" {
  description = "The display name of the created project."
  value       = google_project.project.name
}

output "env_config_snippet" {
  description = "Configuration block ready to be copied into .env for Redwood Retail deployment."
  value       = <<EOT
# Google Cloud Platform Settings
GCP_PROJECT_ID=${google_project.project.project_id}
GCP_REGION=${var.region}
EOT
}

output "next_steps" {
  description = "Instructions to deploy Redwood Retail application infrastructure."
  value       = "Project created successfully. Update your .env with GCP_PROJECT_ID=${google_project.project.project_id}, then run ./deploy.sh"
}
