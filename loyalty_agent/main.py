"""Runtime wiring for the Redwood Retail loyalty offer agent."""

import json
import logging
from typing import Any, Dict, Optional

from loyalty_agent.agent import LoyaltyAgent
from loyalty_agent.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("loyalty_agent.main")


class LoyaltyAgentEngine:
    """Lazily initialised host for a single LoyaltyAgent."""

    def register_operations(self) -> Dict[str, Any]:
        """Registers methods callable by a hosted runtime."""
        return {
            "": ["query"]
        }

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        firestore_database: Optional[str] = None,
        bigquery_dataset: Optional[str] = None,
        reasoning_model: Optional[str] = None,
        cooldown_days: Optional[int] = None,
        agent: Optional[LoyaltyAgent] = None,
    ):
        self.project_id = project_id or config.project_id
        self.region = region or config.region
        self.firestore_database = firestore_database or config.firestore_database
        self.bigquery_dataset = bigquery_dataset or config.bigquery_dataset
        self.reasoning_model = reasoning_model or config.reasoning_model
        self.cooldown_days = cooldown_days if cooldown_days is not None else config.cooldown_days

        self.agent = agent
        self.fs_client = None
        self.bq_client = None
        self.genai_client = None

    def set_up(self) -> LoyaltyAgent:
        """Create the clients and the agent. Safe to call more than once."""
        if self.agent is not None:
            return self.agent

        from google.cloud import bigquery
        from google.cloud import firestore
        from google import genai

        logger.info("Initializing loyalty agent in project %s (%s)...", self.project_id, self.region)

        if self.fs_client is None:
            self.fs_client = firestore.Client(project=self.project_id, database=self.firestore_database)
        if self.bq_client is None:
            self.bq_client = bigquery.Client(project=self.project_id, location=self.region)

        try:
            # Initialise Gemini client with Vertex AI backend.
            self.genai_client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.region,
            )
        except Exception as exc:
            logger.warning("Vertex AI GenAI client unavailable, offers will use deterministic rules: %s", exc)
            self.genai_client = None

        self.agent = LoyaltyAgent(
            firestore_client=self.fs_client,
            bigquery_client=self.bq_client,
            genai_client=self.genai_client,
            project_id=self.project_id,
            dataset_id=self.bigquery_dataset,
            model_name=self.reasoning_model,
            cooldown_days=self.cooldown_days,
        )
        logger.info("Loyalty agent ready (reasoning model: %s).", self.reasoning_model)
        return self.agent

    def query(self, *args, **kwargs) -> Dict[str, Any]:
        """Evaluate one session and report what was decided."""
        agent = self.set_up()

        input_data = dict(kwargs)
        if args and isinstance(args[0], dict):
            input_data.update(args[0])
        elif args and isinstance(args[0], str):
            input_data.setdefault("session_id", args[0])

        session_id = input_data.get("session_id") or input_data.get("sessionId", "")
        if not session_id:
            return {"sessionId": "", "action": "NO_SESSION", "offer": None}

        offer = agent.process_session(str(session_id))
        return {
            "sessionId": str(session_id),
            "action": "OFFER_ISSUED" if offer else "NO_OFFER_ISSUED",
            "offer": offer
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Redwood Retail loyalty offer agent")
    parser.add_argument("--session-id", required=True, help="Customer session to evaluate")
    args = parser.parse_args()

    engine = LoyaltyAgentEngine()
    result = engine.query(session_id=args.session_id)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
