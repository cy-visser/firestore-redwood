# Redwood Retail: Project Creation & Bootstrap Module

This Terraform module automates the provisioning of a fresh Google Cloud Project designed to host the **Redwood Retail** architecture. It establishes foundational services and prepares the environment for application-level provisioning.

---

## Architecture & Design Rationale

Project creation is intentionally decoupled from the core application resources (`../`):

1. **Privilege Separation**: Creating Google Cloud projects and attaching billing accounts requires elevated organizational permissions (`roles/resourcemanager.projectCreator`, `roles/billing.user`) typically tied to an enterprise/sandbox admin account (e.g., `admin@<domain>.altostrat.com`). Application workloads only require project-level `roles/owner` or `roles/editor`.
2. **Lifecycle Decoupling**: Tearing down workload infrastructure (e.g. recreating Firestore databases or draining Dataflow streaming jobs via `./deploy.sh --teardown`) will **not** accidentally destroy the GCP project, billing linkage, or foundational API setups.
3. **Clean Networking Baseline**: Creates the project with `auto_create_network = false`, ensuring the custom VPC and private subnet created by the Redwood workload module can be deployed cleanly without default network collision.

---

## Provisioned Resources

* **`google_project`**: Creates the Google Cloud project with configurable display name, random or explicit project ID, and links the billing account.
* **`google_project_service`**: Bootstraps foundational APIs required before service provisioning:
  * `serviceusage.googleapis.com`
  * `cloudresourcemanager.googleapis.com`
  * `iam.googleapis.com`
* **`google_project_iam_member`**: Optionally grants `roles/owner` to an administrator or service account.

---

## Prerequisites

1. Authenticate with an identity that has project creation and billing assignment rights:
   ```bash
   gcloud auth login admin@<domain>.altostrat.com
   gcloud auth application-default login
   ```
2. Identify your Billing Account ID:
   ```bash
   gcloud billing accounts list
   ```

---

## Usage

### 1. Configure Variables
Copy the example variables file:
```bash
cp terraform.tfvars.example terraform.tfvars
```
Edit `terraform.tfvars` with your billing account ID, organization/folder ID, and owner email:
```hcl
billing_account_id       = "012345-6789AB-CDEF01"
project_prefix           = "redwood-retail"
primary_owner_user_email = "admin@ganeshraja.altostrat.com"
region                   = "europe-west4"
```

### 2. Initialize and Apply
```bash
terraform init
terraform plan
terraform apply
```

### 3. Connect to Application Deployment
Once applied, copy the output `project_id` into your root `.env` file:
```bash
# Example output
GCP_PROJECT_ID="redwood-retail-a1b2c3"
```
Then run the main deployment script from the root of the repository:
```bash
cd ../..
./deploy.sh
```
