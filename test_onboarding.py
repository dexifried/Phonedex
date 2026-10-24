"""Offline tests for first-run PhoneDex onboarding (never dial or deploy)."""
from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import onboarding


class ValidationTests(unittest.TestCase):
    def test_e164(self):
        self.assertEqual(onboarding.e164(" +12025550123 "), "+12025550123")
        for value in ["6139279352", "+0123456789", "+1", "+1202x", "+12025550123\nNEW=BAD"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                onboarding.e164(value)

    def test_allowlist_is_validated_and_deduplicated(self):
        self.assertEqual(onboarding.approved_recipients("+12025550123,+12025550123,+14155550123"),
                         "+12025550123,+14155550123")
        for bad in ("", "+12025550123,bad", "+12025550123\nEXTRA=1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                onboarding.approved_recipients(bad)

    def test_public_ip_rejects_lan_and_reserved(self):
        self.assertEqual(onboarding.public_ipv4(" 8.8.8.8 "), "8.8.8.8")
        for bad in ("192.168.1.3", "10.0.0.5", "127.0.0.1", "::1", "example.com", "203.0.113.99"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                onboarding.public_ipv4(bad)

    def test_sip_uri_rejects_placeholders_and_control_chars(self):
        self.assertEqual(onboarding.sip_target("sips:trunk.example.com:5061"), "sips:trunk.example.com:5061")
        for bad in ("not-sip", "sips:your-trunk.example.invalid:5062", "sip:host\r\nBAD=Y", "sip:<host>"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                onboarding.sip_target(bad)

    def test_remote_worker_endpoint_must_be_exact_gateway_path(self):
        self.assertEqual(
            onboarding.worker_endpoint("https://my-worker.workers.dev/agent/gateway"),
            "https://my-worker.workers.dev/agent/gateway",
        )
        for bad in (
            "http://my-worker.workers.dev/agent/gateway",
            "https://my-worker.workers.dev",
            "https://user:pass@my-worker.workers.dev/agent/gateway",
            "https://your-worker.workers.dev/agent/gateway",
            "https://my-worker.workers.dev/agent/gateway?token=secret",
            "https://my-worker.workers.dev/agent/gateway#fragment",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                onboarding.worker_endpoint(bad)

    def test_config_values_cannot_escape_into_other_lines(self):
        for bad in ("abc\nPHONEDEX_MEDIA_PORT_FORWARDED=1", " hi", "x\r", "'quoted'"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                onboarding.checked_field(bad)


class SetupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def example(self):
        return {
            "PHONEDEX_TERMINATION_URI": "sips:trunk.example.com:5061",
            "PHONEDEX_PUBLIC_IP": "8.8.8.8",
            "PHONEDEX_CALLER_ID": "+12025550123",
            "PHONEDEX_SIP_USERNAME": "test-only",
            "PHONEDEX_SIP_PASSWORD": "not-real-private-string",
            "PHONEDEX_ALLOWED_RECIPIENTS": "+14155550123",
            "PHONEDEX_MEDIA_PORT_FORWARDED": "0",
        }

    def test_save_file_does_not_enable_port_mapping(self):
        path = onboarding.save_env(self.root, self.example())
        content = path.read_text(encoding="utf-8")
        self.assertIn("PHONEDEX_MEDIA_PORT_FORWARDED=0", content)
        self.assertIn("PHONEDEX_ALLOWED_RECIPIENTS=+14155550123", content)
        self.assertIn("PHONEDEX_SIP_PASSWORD=not-real-private-string", content)
        self.assertNotIn("PHONEDEX_GATEWAY_TOKEN=not-real-private-string", content)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_private_config_never_overwritten(self):
        p = self.root / ".env"
        p.write_text("PHONEDEX_SIP_PASSWORD=existing\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            onboarding.save_env(self.root, self.example())
        self.assertEqual(p.read_text(encoding="utf-8"), "PHONEDEX_SIP_PASSWORD=existing\n")

    def test_no_injected_lines_or_unknown_keys(self):
        cfg=self.example()
        cfg["PHONEDEX_SIP_PASSWORD"]="anything\nEVIL=yes"
        with self.assertRaises(ValueError):
            onboarding.save_env(self.root,cfg)
        self.assertFalse((self.root / ".env").exists())
        cfg=self.example()
        cfg["UNRELATED_SERVICE"]="set"
        with self.assertRaises(ValueError):
            onboarding.save_env(self.root,cfg)

    def test_noninteractive_wizard_fails_closed(self):
        with mock.patch("onboarding.sys.stdin") as stdin:
            stdin.isatty.return_value=False
            buf=StringIO()
            with mock.patch("sys.stdout", buf):
                self.assertFalse(onboarding.run_setup(self.root, "local"))
        self.assertFalse((self.root / ".env").exists())

    def test_check_never_prints_secret_or_personal_number(self):
        cfg=self.example()
        cfg["PHONEDEX_GATEWAY_URL"]="https://worker.example.com/agent/gateway"
        cfg["PHONEDEX_GATEWAY_TOKEN"]="secret-shall-never-print"
        with mock.patch("onboarding.missing_deps",return_value=[]), \
             mock.patch("onboarding.codex_oauth_ready",return_value=True), \
             mock.patch("onboarding.shutil.which",return_value="codex"), \
             mock.patch.dict(os.environ,cfg,clear=True):
            output=[]
            self.assertFalse(onboarding.print_readiness("remote",printer=output.append))
            joined="\n".join(output)
            self.assertNotIn(cfg["PHONEDEX_SIP_PASSWORD"],joined)
            self.assertNotIn(cfg["PHONEDEX_GATEWAY_TOKEN"],joined)
            self.assertNotIn(cfg["PHONEDEX_CALLER_ID"],joined)
            self.assertIn("SRTP UDP forwarding", joined)

    def test_remote_worker_is_optional_for_local(self):
        cfg=self.example()
        with mock.patch("onboarding.missing_deps",return_value=[]), \
             mock.patch("onboarding.codex_oauth_ready",return_value=True), \
             mock.patch("onboarding.shutil.which",return_value="codex"):
            items=onboarding.readiness("local",cfg)
            self.assertFalse(any("Cloudflare" in name or "Worker" in name for name,_,__ in items))
            remote=onboarding.readiness("remote",cfg)
            self.assertTrue(any("Worker" in name for name,_,__ in remote))

    def test_wizard_creates_local_config_without_live_actions(self):
        answers=iter(("sips:trunk.example.com:5061", "8.8.8.8",
                      "+12025550123", "tester", "secret-from-hidden-input",
                      "+14155550123"))
        def field_response(*args,**kwargs):
            return next(answers)
        with mock.patch("onboarding.sys.stdin") as stdin, \
             mock.patch("onboarding._ask",side_effect=field_response), \
             mock.patch("sys.stdout",new_callable=StringIO) as output:
            stdin.isatty.return_value=True
            done=onboarding.run_setup(self.root,"local",input_fn=lambda prompt:"yes")
        self.assertTrue(done)
        payload=(self.root/".env").read_text(encoding="utf-8")
        self.assertIn("PHONEDEX_MEDIA_PORT_FORWARDED=0",payload)
        self.assertIn("PHONEDEX_ALLOWED_RECIPIENTS=+14155550123",payload)
        self.assertNotIn("secret-from-hidden-input",output.getvalue())
        self.assertNotIn("PHONEDEX_GATEWAY_URL=https:",payload)

    def test_wizard_user_cancellation_writes_nothing(self):
        with mock.patch("onboarding.sys.stdin") as stdin:
            stdin.isatty.return_value=True
            with mock.patch("sys.stdout",new_callable=StringIO):
                self.assertFalse(onboarding.run_setup(self.root,"local",input_fn=lambda prompt:"n"))
        self.assertFalse((self.root / ".env").exists())


if __name__=="__main__":
    unittest.main()
