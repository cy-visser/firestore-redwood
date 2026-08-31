"""
Google Cloud Service Account & IAM Authentication helper for Firestore Enterprise (MongoDB API).
Uses MONGODB-OIDC and Google Cloud Application Default Credentials (ADC) / OAuth2 access tokens.
"""

import subprocess
import google.auth
import google.auth.transport.requests
from pymongo import MongoClient
from pymongo.auth_oidc import OIDCCallback, OIDCCallbackResult, OIDCCallbackContext


class GoogleOIDCCallback(OIDCCallback):
    """Refreshes and supplies Google OAuth2 access tokens for PyMongo MONGODB-OIDC."""
    def __init__(self):
        self.credentials, self.project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/datastore"]
        )
        self.auth_req = google.auth.transport.requests.Request()

    def fetch(self, context: OIDCCallbackContext) -> OIDCCallbackResult:
        self.credentials.refresh(self.auth_req)
        return OIDCCallbackResult(access_token=self.credentials.token)


def get_firestore_mongo_client(project_id="elevate-cyvisser", database_id="redwood", region="europe-west4"):
    """
    Returns an authenticated PyMongo MongoClient connected to Firestore Enterprise
    via MONGODB-OIDC using the current Google Cloud IAM / Service Account context.
    """
    cmd = [
        "gcloud", "firestore", "databases", "describe",
        f"--database={database_id}",
        f"--project={project_id}",
        "--format=value(uid)"
    ]
    uid = subprocess.check_output(cmd, text=True).strip()
    host = f"{uid}.{region}.firestore.goog"
    uri = f"mongodb://{host}:443/{database_id}?loadBalanced=true&tls=true&retryWrites=false&authMechanism=MONGODB-OIDC"

    return MongoClient(
        uri,
        authMechanismProperties={"OIDC_CALLBACK": GoogleOIDCCallback()},
        serverSelectionTimeoutMS=8000
    )
