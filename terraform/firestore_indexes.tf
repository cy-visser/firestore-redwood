# ==============================================================================
# Firestore Composite Indexes & TTL Policies (Loyalty Agent / Issue #4)
# ==============================================================================

# Composite index for mobile app active offers query:
# WHERE customerId == :cid AND status == 'ACTIVE' ORDER BY createdAt DESC
resource "google_firestore_index" "loyalty_offers_active_by_customer" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "loyalty_offers"

  fields {
    field_path = "customerId"
    order      = "ASCENDING"
  }

  fields {
    field_path = "status"
    order      = "ASCENDING"
  }

  fields {
    field_path = "createdAt"
    order      = "DESCENDING"
  }

  depends_on = [
    terraform_data.firestore_database
  ]
}

# Composite index for Agent listener / query processing:
# WHERE agentProcessingStatus == 'PENDING' ORDER BY loginTimestamp ASC
resource "google_firestore_index" "customer_sessions_pending_queue" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "customer_sessions"

  fields {
    field_path = "agentProcessingStatus"
    order      = "ASCENDING"
  }

  fields {
    field_path = "loginTimestamp"
    order      = "ASCENDING"
  }

  depends_on = [
    terraform_data.firestore_database
  ]
}

# Composite index for customer login session history:
# WHERE customerId == :cid ORDER BY loginTimestamp DESC
resource "google_firestore_index" "customer_sessions_by_customer" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "customer_sessions"

  fields {
    field_path = "customerId"
    order      = "ASCENDING"
  }

  fields {
    field_path = "loginTimestamp"
    order      = "DESCENDING"
  }

  depends_on = [
    terraform_data.firestore_database
  ]
}

# TTL policy on customer_sessions collection (30-day session document purge)
resource "google_firestore_field" "customer_sessions_ttl" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "customer_sessions"
  field      = "expireAt"

  ttl_config {}

  depends_on = [
    terraform_data.firestore_database
  ]
}

# TTL policy on loyalty_offers collection (90-day post-expiration purge)
resource "google_firestore_field" "loyalty_offers_ttl" {
  project    = var.project_id
  database   = var.firestore_database_id
  collection = "loyalty_offers"
  field      = "ttlExpiryAt"

  ttl_config {}

  depends_on = [
    terraform_data.firestore_database
  ]
}
