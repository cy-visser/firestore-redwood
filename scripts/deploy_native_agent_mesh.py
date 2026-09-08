#!/usr/bin/env python3
"""
Deploy Native Agent Mesh on Google Cloud Agent Runtime (Vertex AI Reasoning Engines).

Packages and deploys all 6 standalone agents natively:
1. Phase 1 (Parallel): Cooldown, Churn, Friction, Synthesis, and Fulfillment agents.
2. Phase 2: Retention Orchestrator configured with the 5 native domain agent endpoints.

Zero BYOC containers, zero web server boilerplate, native managed lifecycle.
"""

import sys
import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vertexai
from vertexai.preview import reasoning_engines
from loyalty_agent.main import StandaloneAgentEngine
from loyalty_agent.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("deploy_native_agent_mesh")

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "redwood-retail-949ec9")
REGION = os.getenv("GCP_REGION", "europe-west4")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", f"gs://{PROJECT_ID}-redwood-retail-{REGION}")
OUTPUT_REGISTRY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployed_native_agents.json")

DEPENDENCY_REQUIREMENTS = [
    "google-cloud-aiplatform>=1.70.0",
    "google-cloud-firestore>=2.19.0",
    "google-cloud-bigquery>=3.25.0",
    "google-genai>=0.1.1",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "cloudpickle>=3.0.0",
]


def deploy_single_agent(
    role: str,
    display_name: str,
    description: str,
    extra_engine_kwargs: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Builds, packages, and deploys a single native agent to Agent Runtime."""
    extra_kwargs = extra_engine_kwargs or {}
    logger.info("🚀 [%s] Staging and serializing StandaloneAgentEngine...", role.upper())

    engine_instance = StandaloneAgentEngine(
        role=role,
        project_id=PROJECT_ID,
        region=REGION,
        firestore_database=config.firestore_database,
        bigquery_dataset=config.bigquery_dataset,
        **extra_kwargs
    )

    t0 = time.time()
    re = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=engine_instance,
        requirements=DEPENDENCY_REQUIREMENTS,
        extra_packages=["loyalty_agent"],
        gcs_dir_name=f"reasoning_engine_{role}",
        display_name=display_name,
        description=description,
    )
    elapsed = time.time() - t0

    resource_name = re.resource_name
    engine_id = resource_name.split("/")[-1]
    logger.info("✅ [%s] Deployed in %.1fs -> %s (ID: %s)", role.upper(), elapsed, resource_name, engine_id)

    return {
        "role": role,
        "display_name": display_name,
        "resource_name": resource_name,
        "engine_id": engine_id,
        "elapsed_seconds": round(elapsed, 1)
    }


def main():
    logger.info("=" * 70)
    logger.info("🌲 REDWOOD RETAIL: Native Agent Runtime Mesh Deployment")
    logger.info("=" * 70)
    logger.info("Project:        %s", PROJECT_ID)
    logger.info("Region:         %s", REGION)
    logger.info("Staging Bucket: %s", STAGING_BUCKET)
    logger.info("Output File:    %s", OUTPUT_REGISTRY_FILE)
    logger.info("-" * 70)

    vertexai.init(
        project=PROJECT_ID,
        location=REGION,
        staging_bucket=STAGING_BUCKET
    )

    domain_specs = [
        {
            "role": "cooldown",
            "display_name": "redwood-cooldown-agent-native",
            "description": "Redwood Retail Cooldown Policy Agent (Native Agent Runtime)",
            "extra_engine_kwargs": {"cooldown_days": config.cooldown_days}
        },
        {
            "role": "churn",
            "display_name": "redwood-churn-agent-native",
            "description": "Redwood Retail Churn Intelligence Agent (Native Agent Runtime)",
            "extra_engine_kwargs": {}
        },
        {
            "role": "friction",
            "display_name": "redwood-friction-agent-native",
            "description": "Redwood Retail Customer Friction Agent (Native Agent Runtime)",
            "extra_engine_kwargs": {}
        },
        {
            "role": "synthesis",
            "display_name": "redwood-synthesis-agent-native",
            "description": "Redwood Retail Offer Synthesis Agent (Native Agent Runtime)",
            "extra_engine_kwargs": {}
        },
        {
            "role": "fulfillment",
            "display_name": "redwood-fulfillment-agent-native",
            "description": "Redwood Retail Offer Fulfillment Agent (Native Agent Runtime)",
            "extra_engine_kwargs": {"cooldown_days": config.cooldown_days}
        }
    ]

    registry: Dict[str, Any] = {
        "project_id": PROJECT_ID,
        "region": REGION,
        "staging_bucket": STAGING_BUCKET,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agents": {}
    }

    # --------------------------------------------------------------------------
    # Phase 1: Deploy 5 Domain Agents in Parallel
    # --------------------------------------------------------------------------
    logger.info("\n📦 Phase 1: Deploying 5 Domain Agents concurrently...")
    p1_start = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {
            executor.submit(
                deploy_single_agent,
                spec["role"],
                spec["display_name"],
                spec["description"],
                spec["extra_engine_kwargs"]
            ): spec["role"]
            for spec in domain_specs
        }

        for future in as_completed(future_map):
            role = future_map[future]
            try:
                res = future.result()
                registry["agents"][role] = res
            except Exception as exc:
                logger.exception("❌ Error deploying domain agent [%s]: %s", role.upper(), exc)
                sys.exit(1)

    logger.info("🎉 Phase 1 completed in %.1fs. All 5 domain agents deployed.", time.time() - p1_start)

    # --------------------------------------------------------------------------
    # Phase 2: Deploy Retention Orchestrator with Domain Agent Endpoints
    # --------------------------------------------------------------------------
    logger.info("\n📦 Phase 2: Deploying Retention Orchestrator Agent...")
    orch_kwargs = {
        "cooldown_agent_url": registry["agents"]["cooldown"]["resource_name"],
        "churn_agent_url": registry["agents"]["churn"]["resource_name"],
        "friction_agent_url": registry["agents"]["friction"]["resource_name"],
        "synthesis_agent_url": registry["agents"]["synthesis"]["resource_name"],
        "fulfillment_agent_url": registry["agents"]["fulfillment"]["resource_name"],
    }

    orch_res = deploy_single_agent(
        role="orchestrator",
        display_name="redwood-retention-orchestrator-native",
        description="Redwood Retail Retention Orchestrator Agent (Native Agent Runtime)",
        extra_engine_kwargs=orch_kwargs
    )
    registry["agents"]["orchestrator"] = orch_res

    # --------------------------------------------------------------------------
    # Save Registry File
    # --------------------------------------------------------------------------
    with open(OUTPUT_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)

    logger.info("\n" + "=" * 70)
    logger.info("🌲 DEPLOYMENT SUMMARY (6 Native Agents on Agent Runtime)")
    logger.info("=" * 70)
    for role, data in registry["agents"].items():
        logger.info(" • %-14s: %s (ID: %s)", role.upper(), data["display_name"], data["engine_id"])
    logger.info("Registry saved to: %s", OUTPUT_REGISTRY_FILE)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
