"""Offline safety and consent regression tests for optional personal calling."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phonebridge.calling_profile import (
    ProfileError, load_settings, personalization_prompt, read_profile,
    save_profile_field, scoped_disclosure, update_setting,
)


class CallerPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "preferences.json"

    def test_off_by_default_and_no_voice_context(self):
        self.assertFalse(load_settings(self.path)["enabled"])
        self.assertEqual(personalization_prompt(self.path), "")

    def test_opt_in_operator_naming(self):
        update_setting("assistant_name", "Muse", path=self.path)
        self.assertEqual(personalization_prompt(self.path), "")
        update_setting("enabled", "on", path=self.path)
        update_setting("answering_instructions", "Keep answers short and task-focused", path=self.path)
        prompt=personalization_prompt(self.path)
        self.assertIn("Muse", prompt)
        self.assertIn("Do not impersonate a human", prompt)
        self.assertIn("Keep answers short", prompt)
        update_setting("enabled", "off", path=self.path)
        self.assertEqual(personalization_prompt(self.path), "")

    def test_per_agent_config_isolated_and_safe_slug(self):
        import phonebridge.calling_profile as mod
        with patch.object(mod, "ROOT", Path(self.temp.name)):
            update_setting("assistant_name", "Muse", agent="muse")
            update_setting("enabled", "on", agent="muse")
            update_setting("assistant_name", "Mechanic", agent="mechanic")
            update_setting("enabled", "on", agent="mechanic")
            self.assertIn("Muse", personalization_prompt(agent="muse"))
            self.assertNotIn("Mechanic", personalization_prompt(agent="muse"))
            self.assertIn("Mechanic", personalization_prompt(agent="mechanic"))
            with self.assertRaises(ProfileError):
                update_setting("enabled", "on", agent="../evil")
            with self.assertRaises(ProfileError):
                load_settings(agent="Not Valid")

    def test_strict_preference_configuration(self):
        with self.assertRaises(ProfileError):
            update_setting("unknown", "bad", path=self.path)
        with self.assertRaises(ProfileError):
            update_setting("enabled", "yes", path=self.path)
        with self.assertRaises(ProfileError):
            update_setting("caller_accent", "x" * 100, path=self.path)
        self.path.write_text(json.dumps({"enabled": "true"}))
        with self.assertRaises(ProfileError):
            load_settings(self.path)


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography not installed")
class EncryptedProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "calling.enc.json"
        self.passphrase = "test-only-strong-secret-42"

    def test_round_trip_encrypted_at_rest(self):
        save_profile_field("full_name", "Private Test Person", self.passphrase, path=self.path)
        save_profile_field("dental_insurance_carrier", "Example Dental Inc", self.passphrase, path=self.path)
        raw = self.path.read_text()
        self.assertNotIn("Private Test Person", raw)
        self.assertNotIn("Example Dental Inc", raw)
        self.assertEqual(read_profile(self.passphrase, self.path)["full_name"], "Private Test Person")
        self.assertIn("ciphertext", json.loads(raw))
        self.assertNotIn("nonce_b64", raw)

    def test_wrong_passphrase_and_scoped_sharing(self):
        save_profile_field("full_name", "Private Test Person", self.passphrase, path=self.path)
        save_profile_field("phone", "+17055550123", self.passphrase, path=self.path)
        with self.assertRaises(ProfileError):
            read_profile("the-wrong-passphrase-99", self.path)
        disclosure = scoped_disclosure(["full_name"], self.passphrase, path=self.path)
        self.assertIn("Private Test Person", disclosure)
        self.assertNotIn("+17055550123", disclosure)
        self.assertNotIn("password:", disclosure)
        with self.assertRaises(ProfileError):
            scoped_disclosure(["full_name", "not_a_field"], self.passphrase, path=self.path)
        with self.assertRaises(ProfileError):
            scoped_disclosure(["email"], self.passphrase, path=self.path)
        with self.assertRaises(ProfileError):
            scoped_disclosure(["full_name", "full_name"], self.passphrase, path=self.path)

    def test_empty_string_removes_field(self):
        save_profile_field("full_name", "Private Test Person", self.passphrase, path=self.path)
        save_profile_field("full_name", "", self.passphrase, path=self.path)
        self.assertNotIn("full_name", read_profile(self.passphrase, self.path))

    def test_e164_format_and_passphrase_minimum(self):
        with self.assertRaises(ProfileError):
            save_profile_field("phone", "7055550123", self.passphrase, path=self.path)
        with self.assertRaises(ProfileError):
            save_profile_field("full_name", "Alice", "short", path=self.path)


if __name__ == "__main__":
    unittest.main()
