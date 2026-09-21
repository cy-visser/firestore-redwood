"""
Configuration module for Redwood Retail Autonomous Loyalty Offer Agent.
Aligns with Software Design Document (SDD) specifications.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

# Values are read from the process environment. deploy.sh exports them, Cloud
# Run injects them, but a developer running a script by hand has only the .env
# file at the repository root, so load that first without letting it override
# anything the environment already set.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # python-dotenv is optional at runtime
    pass


class ConfigurationError(RuntimeError):
    """Raised when a required environment setting is missing."""


def _require_project_id() -> str:
    """Resolve the target project, refusing to guess one.

    This used to default to a specific project id, which meant a misconfigured
    environment silently read and wrote someone else's Firestore and BigQuery
    instead of failing.
    """
    project = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
    if not project:
        raise ConfigurationError(
            "No Google Cloud project configured. Set GCP_PROJECT_ID (or "
            "GCP_PROJECT) in the environment or in the .env file at the "
            "repository root."
        )
    return project


@dataclass(frozen=True)
class AgentConfig:
    # Google Cloud Environment
    project_id: str = field(default_factory=_require_project_id)
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

    # AI Reasoning Engine Standard.
    # Probed against the live Vertex AI endpoint in project elevate-cyvisser on
    # 2026-09-21: gemini-3.8-flash returns HTTP 404 in both europe-west4 and
    # global, while gemini-2.5-flash and gemini-2.5-pro return 200 in
    # europe-west4. Do not raise this number without re-probing the endpoint.
    reasoning_model: str = field(default_factory=lambda: os.getenv("REASONING_MODEL") or "gemini-2.5-flash")

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
