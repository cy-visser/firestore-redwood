variable "project_id" {
  description = "Explicit Google Cloud Project ID to create. If left empty, an ID will be generated using project_prefix and a random suffix."
  type        = string
  default     = ""
}

variable "project_prefix" {
  description = "Prefix for the automatically generated Project ID if project_id is empty."
  type        = string
  default     = "redwood-retail"
}

variable "project_name" {
  description = "Display name for the Google Cloud project."
  type        = string
  default     = "Redwood Retail CDC"
}

variable "billing_account_id" {
  description = "The Google Cloud Billing Account ID to link to the new project (e.g., 012345-6789AB-CDEF01)."
  type        = string
}

variable "org_id" {
  description = "The numeric Organization ID to place the project under. Leave empty if using folder_id or if deploying standalone."
  type        = string
  default     = ""
}

variable "folder_id" {
  description = "The Folder ID (numeric or folders/12345) to place the project under. Takes precedence over org_id."
  type        = string
  default     = ""
}

variable "region" {
  description = "Default deployment region for resources."
  type        = string
  default     = "europe-west4"
}

variable "primary_owner_user_email" {
  description = "Email of the primary user or admin to grant roles/owner on the newly created project."
  type        = string
  default     = ""
}

variable "deletion_policy" {
  description = "The deletion policy for the project. Valid values are PREVENT or DELETE."
  type        = string
  default     = "DELETE"
}
