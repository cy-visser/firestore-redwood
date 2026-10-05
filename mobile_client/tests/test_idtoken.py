"""
Unit tests for ID token minting.

The behaviour under test is which credential path gets chosen, because
choosing wrong fails in a way that is very hard to read: the metadata server
on a Compute Engine developer workstation hands back a perfectly valid token
for the *workstation's* service account, and Cloud Run then answers 403. That
looks exactly like a missing IAM binding, and it was diagnosed as one for a
while before the real cause turned up.
"""

import unittest
from unittest.mock import MagicMock, patch

# Imported for its side effect: google.auth does not expose
# `impersonated_credentials` as an attribute until something imports the
# submodule, and the patches below address it by that name.
import google.auth.impersonated_credentials  # noqa: F401

from mobile_client.backend import idtoken


class OnCloudRunTest(unittest.TestCase):
    def test_keys_off_k_service(self):
        # Cloud Run sets K_SERVICE on every revision; nothing else does.
        with patch.dict("os.environ", {"K_SERVICE": "redwood-app"}, clear=False):
            self.assertTrue(idtoken._on_cloud_run())

    def test_false_without_k_service(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(idtoken._on_cloud_run())


class MintTest(unittest.TestCase):
    """``_mint`` chooses between the metadata server and impersonation."""

    def setUp(self):
        idtoken.clear_cache()
        self.addCleanup(idtoken.clear_cache)

        self.fetch_id_token = patch(
            "google.oauth2.id_token.fetch_id_token", return_value="metadata-token"
        ).start()
        self.addCleanup(patch.stopall)

        self.default = patch(
            "google.auth.default", return_value=(MagicMock(), "test-prj")
        ).start()

        impersonated = patch("google.auth.impersonated_credentials").start()
        self.id_token_credentials = impersonated.IDTokenCredentials
        self.id_token_credentials.return_value.token = "impersonated-token"
        self.id_token_credentials.return_value.expiry = None
        self.impersonated = impersonated

    def test_on_cloud_run_uses_the_metadata_server(self):
        with patch.dict("os.environ", {"K_SERVICE": "redwood-app"}, clear=False):
            token, _ = idtoken._mint("https://svc.run.app")

        self.assertEqual(token, "metadata-token")
        self.id_token_credentials.assert_not_called()

    def test_off_cloud_run_impersonates_even_when_metadata_would_answer(self):
        # This is the whole point: a Cloudtop has a metadata server, and using
        # it would mint a token for the wrong principal.
        env = {"PIPELINE_SERVICE_ACCOUNT": "pipe-sa", "GCP_PROJECT_ID": "test-prj"}
        with patch.dict("os.environ", env, clear=True):
            token, _ = idtoken._mint("https://svc.run.app")

        self.assertEqual(token, "impersonated-token")
        self.fetch_id_token.assert_not_called()
        self.assertEqual(
            self.impersonated.Credentials.call_args.kwargs["target_principal"],
            "pipe-sa@test-prj.iam.gserviceaccount.com",
        )
        # Without the email claim Cloud Run has no principal to authorise.
        self.assertTrue(self.id_token_credentials.call_args.kwargs["include_email"])

    def test_falls_back_to_metadata_when_no_impersonation_target(self):
        # A plain GCE or GKE deployment: no K_SERVICE, no configured account,
        # but the metadata server is the correct answer there.
        with patch.dict("os.environ", {}, clear=True):
            token, _ = idtoken._mint("https://svc.run.app")

        self.assertEqual(token, "metadata-token")

    def test_reports_clearly_when_nothing_can_mint(self):
        self.fetch_id_token.side_effect = RuntimeError("no metadata server")

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                idtoken._mint("https://svc.run.app")

        message = str(caught.exception)
        self.assertIn("CHURN_INVOKER_SERVICE_ACCOUNT", message)
        self.assertIn("no metadata server", message)

    def test_explicit_invoker_account_wins(self):
        env = {
            "CHURN_INVOKER_SERVICE_ACCOUNT": "explicit@test-prj.iam.gserviceaccount.com",
            "PIPELINE_SERVICE_ACCOUNT": "pipe-sa",
        }
        with patch.dict("os.environ", env, clear=True), \
                patch.object(idtoken, "_IMPERSONATE_SA", env["CHURN_INVOKER_SERVICE_ACCOUNT"]):
            idtoken._mint("https://svc.run.app")

        self.assertEqual(
            self.impersonated.Credentials.call_args.kwargs["target_principal"],
            "explicit@test-prj.iam.gserviceaccount.com",
        )


class CacheTest(unittest.TestCase):
    def setUp(self):
        idtoken.clear_cache()
        self.addCleanup(idtoken.clear_cache)

    def test_a_cached_token_is_reused(self):
        # A demo reset makes two churn calls back to back; re-minting per call
        # adds a round trip to a button the presenter is standing in front of.
        with patch.object(idtoken, "_mint", return_value=("tok", 1e12)) as mint:
            self.assertEqual(idtoken.fetch("https://svc.run.app"), "tok")
            self.assertEqual(idtoken.fetch("https://svc.run.app"), "tok")

        mint.assert_called_once()

    def test_an_expiring_token_is_reminted(self):
        import time

        # Inside the skew window, so it must not be handed out again.
        expiring = time.time() + (idtoken._EXPIRY_SKEW_SECONDS / 2)
        with patch.object(idtoken, "_mint", return_value=("tok", expiring)) as mint:
            idtoken.fetch("https://svc.run.app")
            idtoken.fetch("https://svc.run.app")

        self.assertEqual(mint.call_count, 2)

    def test_tokens_are_cached_per_audience(self):
        with patch.object(idtoken, "_mint", return_value=("tok", 1e12)) as mint:
            idtoken.fetch("https://app.run.app")
            idtoken.fetch("https://churn.run.app")

        self.assertEqual(mint.call_count, 2)


if __name__ == "__main__":
    unittest.main()
