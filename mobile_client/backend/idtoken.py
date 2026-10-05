"""Mint an ID token for a private Cloud Run audience.

Two paths, chosen by whether this process *is* the deployed service.

On Cloud Run the metadata server issues the token for the runtime service
account directly, which is one HTTP hop and needs no configuration.

Everywhere else the pipeline service account is impersonated, which is why
local development needs ``roles/iam.serviceAccountTokenCreator`` on that
account. Terraform grants it to everything in ``app_invoker_members``.

Note that the branch is on ``K_SERVICE``, not on whether a metadata server
answers. A developer workstation on Compute Engine has one, and it hands back
a token for the workstation's own service account -- valid, and belonging to a
principal with no run.invoker binding, so the call fails 403 in a way that
looks like a missing IAM grant rather than the wrong identity.

A user credential cannot mint an ID token for an arbitrary audience at all:
the audience of a user ID token is fixed to the OAuth client that issued it.
Hence impersonation rather than simply using ADC.

Tokens are cached until shortly before expiry. A churn run is one request, but
a demo reset makes a second one, and re-minting per call adds a metadata round
trip to a button the presenter is standing in front of.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Tuple

logger = logging.getLogger("redwood-mobile-api.idtoken")

# Re-mint this long before the token actually expires, so a token that is valid
# when we check it is still valid when the far end validates it.
_EXPIRY_SKEW_SECONDS = 60

# audience -> (token, expires_at_epoch)
_CACHE: Dict[str, Tuple[str, float]] = {}
_LOCK = threading.Lock()

# The account impersonated under user ADC. Matches the Terraform
# google_service_account.pipeline_sa, and is overridable for anyone running
# against a differently-named deployment.
_IMPERSONATE_SA = os.getenv("CHURN_INVOKER_SERVICE_ACCOUNT", "")


def _on_cloud_run() -> bool:
    """Whether this process is the deployed service rather than a workstation.

    Cloud Run sets K_SERVICE for every service revision. The test matters
    because "is there a metadata server?" is the wrong question: a developer
    workstation on Compute Engine -- a Cloudtop, for instance -- has one too,
    and it answers with the *workstation's* service account. That token is
    perfectly valid and belongs to a principal with no run.invoker binding, so
    the call fails 403 and the error points at IAM rather than at the identity
    being wrong.
    """
    return bool(os.getenv("K_SERVICE"))


def _mint(audience: str) -> Tuple[str, float]:
    """Return a fresh ID token for ``audience`` and the epoch it expires at."""
    import google.auth
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    request = Request()

    # The metadata-server path: one hop, no configuration, right identity --
    # but only when the runtime service account is the one we want.
    if _on_cloud_run():
        try:
            token = id_token.fetch_id_token(request, audience)
            # fetch_id_token returns the raw JWT without its expiry, and Cloud
            # Run issues these with an hour of life. Cached conservatively
            # rather than decoded: an unverified decode here would be a second
            # place that has to be right about JWT parsing.
            return token, time.time() + 3000
        except Exception as exc:  # noqa: BLE001 - fall through to impersonation
            logger.warning(
                "Metadata ID token unavailable on Cloud Run (%s); "
                "impersonating instead.", exc
            )

    source_credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    target = _IMPERSONATE_SA or _default_pipeline_sa(project)
    if not target:
        # No impersonation target and not on Cloud Run. The metadata server is
        # the only thing left to try; on a plain GCE or GKE deployment it is
        # also the correct answer. On a workstation that happens to be a VM it
        # is not -- it answers for the workstation -- so say so now, because
        # the symptom is a 403 that reads like a missing IAM binding.
        logger.warning(
            "No service account to impersonate; falling back to the metadata "
            "server. If the call returns 403, set PIPELINE_SERVICE_ACCOUNT (or "
            "CHURN_INVOKER_SERVICE_ACCOUNT) to the invoking account."
        )
        try:
            return id_token.fetch_id_token(request, audience), time.time() + 3000
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "No ID token available: not running on Cloud Run, no metadata "
                f"server ({exc}), and no service account to impersonate. Set "
                "CHURN_INVOKER_SERVICE_ACCOUNT to the pipeline service "
                "account's email."
            ) from exc

    from google.auth import impersonated_credentials

    delegated = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=target,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    credentials = impersonated_credentials.IDTokenCredentials(
        delegated, target_audience=audience, include_email=True
    )
    credentials.refresh(request)

    expiry = credentials.expiry.timestamp() if credentials.expiry else time.time() + 3000
    return credentials.token, expiry


def _default_pipeline_sa(project: str | None) -> str:
    """Guess the pipeline service account from .env, then from the project.

    ``PIPELINE_SERVICE_ACCOUNT`` is the account id rather than the email --
    that is how .env and Terraform both spell it -- so the domain is appended
    here.
    """
    account_id = (
        os.getenv("PIPELINE_SERVICE_ACCOUNT")
        or os.getenv("DATAFLOW_SERVICE_ACCOUNT")
        or ""
    ).strip()
    if not account_id:
        return ""
    if "@" in account_id:
        return account_id
    project_id = os.getenv("GCP_PROJECT_ID") or project or ""
    return f"{account_id}@{project_id}.iam.gserviceaccount.com" if project_id else ""


def fetch(audience: str) -> str:
    """An ID token for ``audience``, cached until shortly before it expires.

    Blocking, and called from a thread: both credential paths do network IO.
    """
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(audience)
        if cached and cached[1] - _EXPIRY_SKEW_SECONDS > now:
            return cached[0]

    token, expires_at = _mint(audience)

    with _LOCK:
        _CACHE[audience] = (token, expires_at)
    return token


def clear_cache() -> None:
    """Drop every cached token. For tests."""
    with _LOCK:
        _CACHE.clear()
