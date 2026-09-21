#!/usr/bin/env python3
"""
Deploy the loyalty agent to Vertex AI Agent Engine.

Agent Engine is given the LoyaltyAgentEngine object from loyalty_agent.main
directly rather than a container. That class already implements the contract
the runtime expects, set_up() to build clients and query() to evaluate one
session, so there is nothing to adapt. The container route would mean writing
and maintaining an HTTP server whose only job is to call query().

Deployment is keyed on display name. A previous version of this stack recorded
the deployed engine id in Terraform as a literal default, which went stale the
moment the engine was redeployed and pointed at a resource in a different
project. Nothing here or in the bridge records an engine id: both resolve the
engine by display name at run time, so a redeploy cannot leave a dangling
reference behind.

Re-running this updates the existing engine in place rather than creating a
second one with the same name.

Usage:
    python scripts/deploy_agent_engine.py
    python scripts/deploy_agent_engine.py --display-name redwood-loyalty-agent
    python scripts/deploy_agent_engine.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

# Pinned to what the agent imports at run time. Leaving these unpinned would
# let a future release of any of them change behaviour on a redeploy that was
# only meant to pick up an agent code change.
AGENT_REQUIREMENTS = [
    "google-cloud-aiplatform>=1.60.0",
    "google-cloud-firestore>=2.14.0",
    "google-cloud-bigquery>=3.14.0",
    "google-genai>=1.0.0",
    "pydantic>=2.0.0",
]

DEFAULT_DISPLAY_NAME = "redwood-loyalty-agent"


def find_engine(display_name: str):
    """Return the engine with this display name, or None.

    Agent Engine does not enforce unique display names, so this also guards
    against silently updating an arbitrary one of several duplicates.
    """
    from vertexai import agent_engines

    matches = [e for e in agent_engines.list() if e.display_name == display_name]
    if len(matches) > 1:
        raise SystemExit(
            f"{len(matches)} engines already share the display name "
            f"'{display_name}'. Delete the extras before deploying, since the "
            f"bridge resolves the engine by name and cannot choose between them."
        )
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy the loyalty agent to Vertex AI Agent Engine"
    )
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"))
    parser.add_argument("--region", default=os.getenv("GCP_REGION"))
    parser.add_argument(
        "--staging-bucket",
        default=os.getenv("AGENT_STAGING_BUCKET"),
        help="GCS bucket for deployment artifacts; defaults to the pipeline bucket",
    )
    parser.add_argument(
        "--display-name",
        default=os.getenv("AGENT_DISPLAY_NAME", DEFAULT_DISPLAY_NAME),
    )
    parser.add_argument(
        "--service-account",
        default=os.getenv("AGENT_SERVICE_ACCOUNT"),
        help=(
            "Service account email the engine runs as. Defaults to the "
            "pipeline account derived from PIPELINE_SERVICE_ACCOUNT."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deployed and exit")
    args = parser.parse_args()

    for name, value in (("--project", args.project), ("--region", args.region)):
        if not value:
            raise SystemExit(f"{name} is required (set it in .env or pass it explicitly).")

    # The pipeline bucket already exists and is in the right region. Its name is
    # derived the same way terraform/storage.tf derives it.
    staging = args.staging_bucket
    if not staging:
        prefix = os.getenv("GCS_BUCKET_PREFIX")
        if not prefix:
            raise SystemExit(
                "No staging bucket. Set AGENT_STAGING_BUCKET, or GCS_BUCKET_PREFIX "
                "so it can be derived the way Terraform derives it."
            )
        staging = f"gs://{args.project}-{prefix}-{args.region}"
    if not staging.startswith("gs://"):
        staging = f"gs://{staging}"

    print("=" * 65)
    print(" Redwood Retail: deploy loyalty agent to Agent Engine")
    print("=" * 65)
    print(f"Project:       {args.project}")
    print(f"Region:        {args.region}")
    print(f"Staging:       {staging}")
    print(f"Display name:  {args.display_name}")
    print("=" * 65)

    if args.dry_run:
        print("\nDry run. Requirements that would be installed:")
        for req in AGENT_REQUIREMENTS:
            print(f"   {req}")
        print("\nPackage that would be uploaded: loyalty_agent/")
        return 0

    import vertexai
    from vertexai import agent_engines

    # extra_packages paths are uploaded with their directory structure intact,
    # so an absolute path puts the package several directories deep in the
    # runtime and `import loyalty_agent` fails. The path has to be relative to
    # the working directory, which means being in the repository root.
    os.chdir(REPO_ROOT)

    vertexai.init(project=args.project, location=args.region, staging_bucket=staging)

    from loyalty_agent.main import LoyaltyAgentEngine

    # Construct with no clients attached. set_up() builds them inside the
    # runtime, which is what keeps this object small enough to ship and stops a
    # local credential from being captured in the deployed payload.
    engine_object = LoyaltyAgentEngine()

    existing = find_engine(args.display_name)

    # The agent reads all of this from the environment. On a workstation it
    # comes from .env, but the deployed runtime has no .env, and config is
    # built at import time, so anything missing here stops the engine from
    # starting at all. That failure surfaces on the create call as a bare
    # "failed to start and cannot serve traffic" with the real cause only in
    # Cloud Logging, so it is worth being explicit.
    env_vars = {
        "GCP_PROJECT_ID": args.project,
        "GCP_REGION": args.region,
        "FIRESTORE_DATABASE_ID": os.getenv("FIRESTORE_DATABASE_ID", "redwood"),
        "BIGQUERY_DATASET": os.getenv("BIGQUERY_DATASET", "redwood_retail"),
        "REASONING_MODEL": os.getenv("REASONING_MODEL", "gemini-2.5-flash"),
    }

    # Run as the pipeline account. The default Agent Engine service agent can
    # serve traffic but has no Firestore or BigQuery access, so the agent would
    # deploy cleanly and then fail on its first real session.
    service_account = args.service_account
    if not service_account:
        sa_name = (
            os.getenv("PIPELINE_SERVICE_ACCOUNT")
            or os.getenv("DATAFLOW_SERVICE_ACCOUNT")
        )
        if sa_name:
            service_account = (
                sa_name
                if "@" in sa_name
                else f"{sa_name}@{args.project}.iam.gserviceaccount.com"
            )

    print("\nRuntime environment:")
    for key, value in env_vars.items():
        print(f"   {key}={value}")
    print(f"   service account: {service_account or '(Agent Engine default)'}")

    if existing is None:
        print("\nCreating a new Agent Engine deployment. This takes a few minutes...")
        remote = agent_engines.create(
            engine_object,
            display_name=args.display_name,
            description=(
                "Redwood Retail loyalty offer agent. Evaluates a customer session "
                "against BigQuery churn risk and issues a loyalty offer when the "
                "risk and cooldown rules allow it."
            ),
            requirements=AGENT_REQUIREMENTS,
            extra_packages=["loyalty_agent"],
            env_vars=env_vars,
            service_account=service_account,
        )
    else:
        print(f"\nUpdating existing engine {existing.resource_name}...")
        remote = existing.update(
            agent_engine=engine_object,
            requirements=AGENT_REQUIREMENTS,
            extra_packages=["loyalty_agent"],
            env_vars=env_vars,
            service_account=service_account,
        )

    print("\nDeployed.")
    print(f"   Resource name: {remote.resource_name}")
    print(f"   Display name:  {args.display_name}")
    print(
        "\nThe bridge resolves this engine by display name, so no id needs to be "
        "recorded anywhere."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
