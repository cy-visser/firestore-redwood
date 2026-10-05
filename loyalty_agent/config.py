"""Configuration module for Redwood Retail Autonomous Loyalty Offer Agent."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

# Load .env without overriding existing environment variables.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass


class ConfigurationError(RuntimeError):
    """Raised when a required environment setting is missing."""


def _require_project_id() -> str:
    """Resolve the target Google Cloud project ID."""
    project = (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
    )
    if not project:
        raise ConfigurationError(
            "No Google Cloud project configured. Set GCP_PROJECT_ID (or "
            "GCP_PROJECT, or GOOGLE_CLOUD_PROJECT) in the environment or in "
            "the .env file at the repository root."
        )
    return project


@dataclass(frozen=True)
class AgentConfig:
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

    # Reasoning model
    reasoning_model: str = field(default_factory=lambda: os.getenv("REASONING_MODEL") or "gemini-2.5-flash")

    # Retention Guardrails & Cooldown
    cooldown_days: int = 7
    offer_validity_days: int = 14
    session_ttl_days: int = 30
    offer_audit_ttl_days: int = 90

    # Follow-up offers.
    #
    # A redeemed offer does not start a cooldown. The cooldown exists to stop
    # us stacking discounts on a customer who is ignoring them, not to punish
    # one who responded; a customer who spent an offer and is still HIGH or
    # CRITICAL is precisely the one worth another. What redemption does do is
    # step the next ceiling down and count against the cap below, so the
    # sequence terminates instead of discounting forever.
    max_followup_offers: int = 1
    followup_step_down_percent: int = 5
    min_followup_discount_percent: int = 5

    # Churn Risk Thresholds (mirroring BigQuery churn SQL)
    offer_tiers: tuple = ("HIGH", "CRITICAL")
    churn_trigger_threshold: float = 0.60
    churn_critical_threshold: float = 0.80
    churn_moderate_threshold: float = 0.40

    # Escalation on unseen friction.
    #
    # The batch model scores overnight. A complaint filed after that run is,
    # by construction, absent from churn_probability, and it is the only
    # friction worth reacting to -- anything older the model already weighed,
    # and reacting to it again would count the same grievance twice.
    #
    # These tiers may be lifted onto the offer path by that reaction, and
    # nothing else may be. The model is never allowed to lower a tier. LOW is
    # included so the most instructive case is reachable: a loyal customer
    # files one bad review and the agent has to decide whether a single bad
    # day justifies spending margin. Narrow this to MODERATE alone if you want
    # a more predictable stage.
    escalation_candidate_tiers: tuple = field(
        default_factory=lambda: tuple(
            tier.strip().upper()
            for tier in os.getenv(
                "ESCALATION_CANDIDATE_TIERS", "MODERATE,LOW"
            ).split(",")
            if tier.strip()
        )
    )


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
