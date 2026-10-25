"""PhoneDex first-run bootstrap checks, offline and without package installs."""
from __future__ import annotations

import importlib.util
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("phonedex_bootstrap",ROOT/"scripts"/"bootstrap.py")
bootstrap=importlib.util.module_from_spec(spec)
sys.modules["phonedex_bootstrap"]=bootstrap
spec.loader.exec_module(bootstrap)


class BootTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"requirements.txt").write_text("websockets>=15,<17\n",encoding="utf-8")

    def test_read_only_check_never_creates_files_or_makes_network_calls(self):
        output=[]
        with mock.patch.object(bootstrap.subprocess,"run") as run, \
             mock.patch.object(bootstrap,"create_venv") as create:
            checks=bootstrap.report(self.root,output.append)
        self.assertFalse(checks["venv"])
        self.assertFalse(checks["config"])
        self.assertIn("Cloudflare is optional", "\n".join(output))
        run.assert_not_called()
        create.assert_not_called()
        self.assertFalse((self.root/".venv").exists())

    def test_explicit_decline_makes_no_changes(self):
        output=[]
        with mock.patch.object(bootstrap.subprocess,"run") as run, \
             mock.patch.object(bootstrap,"create_venv") as create:
            done=bootstrap.prepare(self.root,confirm=lambda:False,printer=output.append)
        self.assertFalse(done)
        run.assert_not_called()
        create.assert_not_called()
        self.assertFalse((self.root/".env").exists())

    def test_yes_installs_only_inside_local_venv(self):
        py=bootstrap.venv_python(self.root)
        commands=[]
        def fake_create(folder):
            self.assertEqual(Path(folder),self.root)
            py.parent.mkdir(parents=True,exist_ok=True)
            py.write_bytes(b"test-only")
        def fake_run(command,**kwargs):
            commands.append((command,kwargs))
            return mock.Mock(returncode=0)
        with mock.patch.object(bootstrap,"create_venv",side_effect=fake_create),\
             mock.patch.object(bootstrap.subprocess,"run",side_effect=fake_run),\
             mock.patch.object(bootstrap.shutil,"which",return_value=None):
            done=bootstrap.prepare(self.root,confirm=lambda:True,printer=lambda msg:None)
        self.assertTrue(done)
        self.assertEqual(len(commands),1)
        cmd,opts=commands[0]
        self.assertEqual(cmd[:4],[str(py),"-m","pip","install"])
        self.assertEqual(cmd[-1],str(self.root/"requirements.txt"))
        self.assertTrue(opts["check"])
        self.assertEqual(opts["cwd"],str(self.root))
        self.assertFalse((self.root/".env").exists())

    def test_existing_virtualenv_not_automatically_modified(self):
        py=bootstrap.venv_python(self.root)
        py.parent.mkdir(parents=True)
        py.write_bytes(b"test-only")
        with mock.patch.object(bootstrap.subprocess,"run") as run, \
             mock.patch.object(bootstrap,"create_venv") as create:
            self.assertFalse(bootstrap.prepare(self.root,confirm=lambda:False,printer=lambda m:None))
        run.assert_not_called()
        create.assert_not_called()

    def test_broken_dependency_install_is_actionable(self):
        py=bootstrap.venv_python(self.root)
        py.parent.mkdir(parents=True)
        py.write_bytes(b"test-only")
        messages=[]
        with mock.patch.object(bootstrap.subprocess,"run",
                               side_effect=bootstrap.subprocess.CalledProcessError(1,["pip"])):
            self.assertFalse(bootstrap.prepare(self.root,confirm=lambda:True,
                                               printer=messages.append))
        self.assertIn("Dependency installation failed"," ".join(messages))


class DoctorModeTests(unittest.TestCase):
    def test_local_mode_does_not_require_worker_credentials(self):
        import phonedex
        with mock.patch.dict(os.environ,{
            "PHONEDEX_TERMINATION_URI":"sip:termination.example.com",
            "PHONEDEX_PUBLIC_IP":"8.8.8.8",
            "PHONEDEX_CALLER_ID":"+12025550123",
            "PHONEDEX_SIP_USERNAME":"test-only",
            "PHONEDEX_SIP_PASSWORD":"not-a-real-secret"
        },clear=True):
            stdout=StringIO()
            with mock.patch("sys.stdout",stdout), mock.patch(
                    "phonebridge.subscription_live.oauth_check",return_value="CODEX_OAUTH_NOT_READY"):
                phonedex.doctor("local")
            local=stdout.getvalue()
            self.assertIn("CLOUDFLARE_CONTROL=optional",local)
            self.assertIn("CONFIG_MISSING=none",local)
            stdout=StringIO()
            with mock.patch("sys.stdout",stdout), mock.patch(
                    "phonebridge.subscription_live.oauth_check",return_value="CODEX_OAUTH_NOT_READY"):
                phonedex.doctor("remote")
            remote=stdout.getvalue()
            self.assertIn("CLOUDFLARE_CONTROL=required",remote)
            self.assertIn("PHONEDEX_GATEWAY_URL",remote)
            self.assertIn("PHONEDEX_GATEWAY_TOKEN",remote)


if __name__=="__main__":
    unittest.main()
