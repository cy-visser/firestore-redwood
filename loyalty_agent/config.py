"""
Configuration module for Redwood Retail Autonomous Loyalty Offer Agent.
Aligns with Software Design Document (SDD) specifications.
"""

import os
from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class AgentConfig:
    # Google Cloud Environment
    project_id: str = field(default_factory=lambda: os.getenv("GCP_PROJECT") or os.getenv("GCP_PROJECT_ID", "redwood-retail-949ec9"))
    region: str = field(default_factory=lambda: os.getenv("GCP_REGION") or os.getenv("LOCATION", "europe-west4"))
    firestore_database: str = field(default_factory=lambda: os.getenv("FIRESTORE_DATABASE") or os.getenv("FIRESTORE_DATABASE_ID", "redwood"))
    bigquery_dataset: str = field(default_factory=lambda: os.getenv("BIGQUERY_DATASET") or os.getenv("BIGQUERY_DATASET_ID", "redwood_retail"))
    churn_predictions_table: str = field(default_factory=lambda: os.getenv("BIGQUERY_PREDICTIONS_TABLE") or os.getenv("BIGQUERY_TABLE_ID", "customer_churn_risk"))

    @property
    def gcp_project(self) -> str:
        return self.project_id

    @property
    def gcp_region(self) -> str:
        return self.region

    # AI Reasoning Engine Standard
    reasoning_model: str = "gemini-3.8-flash"

    # Retention Guardrails & Cooldown
    cooldown_days: int = 7
    offer_validity_days: int = 14
    session_ttl_days: int = 30
    offer_audit_ttl_days: int = 90

    # Churn Risk Thresholds & Event-Augmented Synthesis
    churn_trigger_threshold: float = 0.50
    churn_critical_threshold: float = 0.75
    acute_friction_boost: float = 0.25

    # Financial Discount Ceilings per Tier (Percentage)
    discount_ceilings: Dict[str, int] = field(default_factory=lambda: {
        "ENTERPRISE_VIP": 25,
        "RETAIL_PRO": 20,
        "STANDARD_LOYALTY": 15,
        "CASUAL": 12,
        "DEFAULT": 15
    })

    margin_floor_percent: float = 10.0


# Global singleton instance
config = AgentConfig()
