"""
Google Cloud Service Account & IAM Authentication helper for Firestore Enterprise Native.
Uses official Google Cloud Firestore Client SDK with Application Default Credentials (ADC).
"""

import os
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

from google.cloud import firestore

# Load environment configuration from .env if present
DEFAULT_PROJECT = os.getenv("GCP_PROJECT_ID") or os.getenv("GCP_PROJECT")
DEFAULT_DATABASE = os.getenv("FIRESTORE_DATABASE_ID") or os.getenv("FIRESTORE_DATABASE")
DEFAULT_REGION = os.getenv("GCP_REGION")


def get_firestore_native_client(project_id=None, database_id=None) -> firestore.Client:
    """
    Returns an authenticated Google Cloud Firestore Native Client
    using Application Default Credentials (ADC) / IAM Service Account.
    """
    project = project_id or DEFAULT_PROJECT
    database = database_id or DEFAULT_DATABASE or "(default)"
    return firestore.Client(project=project, database=database)


def get_firestore_client(project_id=None, database_id=None) -> firestore.Client:
    """Backward-compatible alias for get_firestore_native_client."""
    return get_firestore_native_client(project_id=project_id, database_id=database_id)

