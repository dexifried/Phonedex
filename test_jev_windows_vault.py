"""Offline tests for the optional JevResearch Windows DPAPI bridge."""
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from phonebridge import jev_windows_vault as vault
from phonebridge.jev_shadow import JevShadowObserver

CID = "call_" + "b" * 32

class VaultTests(unittest.TestCase):
    def test_vault_path_uses_per_user_roaming_profile(self):
        with patch.dict(os.environ, {"APPDATA": r"C:\Users\Example\AppData\Roaming"}):
            path = vault.vault_path()
            self.assertEqual(path.name, "provider-key.dpapi")
            self.assertEqual(path.parent.name, "JevResearch")

    def test_explicit_jev_opt_in_uses_existing_vault(self):
        with patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_BACKEND": "jev",
            "PHONEDEX_JEV_USE_JEVRESEARCH_VAULT": "1",
            "TYPESAFE_API_KEY": "",
            "PHONEDEX_JEV_SHADOW_API_KEY": "",
        }, clear=True), patch(
            "phonebridge.jev_shadow.load_jev_research_key_locally",
            return_value="synthetic-test-key-never-real",
        ) as read:
            observer = JevShadowObserver.from_env(CID, "Ask the hotel")
            self.assertIsNotNone(observer)
            self.assertEqual(observer.backend, "jev")
            self.assertEqual(observer.url, "https://api.typesafe.ai/v1/systemone")
            self.assertEqual(observer.api_key, "synthetic-test-key-never-real")
            read.assert_called_once()

    def test_without_explicit_vault_flag_key_is_not_decrypted(self):
        with patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_BACKEND": "jev",
            "TYPESAFE_API_KEY": "",
            "PHONEDEX_JEV_SHADOW_API_KEY": "",
        }, clear=True), patch(
            "phonebridge.jev_shadow.load_jev_research_key_locally",
            side_effect=AssertionError("vault must not be opened"),
        ) as read:
            self.assertIsNone(JevShadowObserver.from_env(CID, "Ask the hotel"))
            read.assert_not_called()

    def test_environment_key_precedes_vault(self):
        with patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_BACKEND": "jev",
            "PHONEDEX_JEV_USE_JEVRESEARCH_VAULT": "1",
            "TYPESAFE_API_KEY": "synthetic-test-key-from-env",
        }, clear=True), patch(
            "phonebridge.jev_shadow.load_jev_research_key_locally",
            side_effect=AssertionError("vault must not be opened"),
        ) as read:
            observer = JevShadowObserver.from_env(CID, "Ask the hotel")
            self.assertEqual(observer.api_key, "synthetic-test-key-from-env")
            read.assert_not_called()

    def test_vault_unlock_failure_fails_closed(self):
        with patch.dict(os.environ, {
            "PHONEDEX_JEV_SHADOW_ENABLED": "1",
            "PHONEDEX_JEV_SHADOW_BACKEND": "jev",
            "PHONEDEX_JEV_USE_JEVRESEARCH_VAULT": "1",
        }, clear=True), patch(
            "phonebridge.jev_shadow.load_jev_research_key_locally",
            side_effect=RuntimeError("not authorized"),
        ):
            self.assertIsNone(JevShadowObserver.from_env(CID, "Ask"))

    def test_local_vault_loader_never_prints_plaintext(self):
        # Mock subprocess; no actual credential is accessed in CI.
        fake = SimpleNamespace(returncode=0, stdout="synthetic-test-key-safe\n", stderr="")
        with patch.object(vault.os, "name", "nt"), patch.object(
            vault, "existing_vault_available", return_value=True), patch.object(
            vault.subprocess, "run", return_value=fake) as run:
            result = vault.load_jev_research_key_locally()
            self.assertEqual(result, "synthetic-test-key-safe")
            kwargs = run.call_args.kwargs
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs["check"])
            self.assertNotIn(result, str(run.call_args))

if __name__ == "__main__":
    unittest.main()
