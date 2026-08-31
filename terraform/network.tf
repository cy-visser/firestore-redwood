# ==============================================================================
# Dedicated VPC Network & Subnetwork for Dataflow Workers
# ==============================================================================

resource "google_compute_network" "dataflow_network" {
  project                 = var.project_id
  name                    = "redwood-dataflow-net"
  auto_create_subnetworks = false

  depends_on = [
    google_project_service.services["compute.googleapis.com"]
  ]
}

resource "google_compute_subnetwork" "dataflow_subnet" {
  project                  = var.project_id
  name                     = "redwood-dataflow-subnet"
  ip_cidr_range            = "10.10.0.0/24"
  region                   = var.region
  network                  = google_compute_network.dataflow_network.id
  private_ip_google_access = true
}

# Cloud Router & NAT for Outbound Worker Access (e.g. PyPI packages / Public APIs)
resource "google_compute_router" "router" {
  project = var.project_id
  name    = "redwood-dataflow-router"
  region  = var.region
  network = google_compute_network.dataflow_network.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "redwood-dataflow-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Firewall rule allowing internal communication between Dataflow worker VMs
resource "google_compute_firewall" "dataflow_internal" {
  project = var.project_id
  name    = "redwood-dataflow-internal"
  network = google_compute_network.dataflow_network.id

  allow {
    protocol = "tcp"
    ports    = ["12345", "12346"]
  }

  source_ranges = ["10.10.0.0/24"]
}
