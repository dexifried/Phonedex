"""PhoneDex first-run helper: private virtualenv, explicit consent, no calls.

Usage:
    python scripts/bootstrap.py          # interactive approval
    python scripts/bootstrap.py --yes    # approve local venv/dependencies only
    python scripts/bootstrap.py --check  # read-only prerequisites
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 11)


def venv_python(root: Path = ROOT) -> Path:
    """Use the platform's virtualenv executable, never global pip."""
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def status(root: Path = ROOT) -> dict[str, bool]:
    return {
        "python": sys.version_info >= MIN_PYTHON,
        "venv": venv_python(root).is_file(),
        "venv_support": importlib.util.find_spec("venv") is not None,
        "codex_cli": shutil.which("codex") is not None,
        "config": (root / ".env").is_file(),
    }


def report(root: Path = ROOT, printer=print) -> dict[str, bool]:
    checks = status(root)
    printer("PhoneDex - First-run preparation (no phone calls)")
    printer(f"  {'OK  ' if checks['python'] else 'TODO'} Python 3.11+")
    printer(f"  {'OK  ' if checks['venv_support'] else 'TODO'} Standard Python venv module (install full Python if missing)")
    printer(f"  {'OK  ' if checks['venv'] else 'TODO'} Isolated Python environment (.venv)")
    printer(f"  {'OK  ' if checks['codex_cli'] else 'TODO'} Codex CLI installed (login is a separate step)")
    printer(f"  {'OK  ' if checks['config'] else 'TODO'} Private .env configuration")
    printer("  Cloudflare is optional for local outbound calls.")
    printer("  No call, networking change, deployment or account login is performed here.")
    return checks


def create_venv(root: Path) -> None:
    # Embedded/stripped-down Python distributions may not ship venv.
    # Delay import so --check is always safe and available.
    import venv
    venv.EnvBuilder(with_pip=True).create(str(root / ".venv"))


def prepare(root: Path = ROOT, *, confirm=None, printer=print) -> bool:
    if sys.version_info < MIN_PYTHON:
        printer("Python 3.11+ is required; install an updated Python interpreter first.")
        return False
    py = venv_python(root)
    if not py.is_file():
        if confirm is None:
            confirm = lambda: input("Create .venv and install Python packages here? [y/N]: ").strip().lower() in ("y", "yes")
        if not confirm():
            printer("No changes made. Run --check for the read-only checklist.")
            return False
        printer("Creating a private .venv under this project...")
        try:
            create_venv(root)
        except (OSError, RuntimeError, ImportError) as exc:
            printer(f"Could not create .venv: {type(exc).__name__}. Install full Python with venv and pip support.")
            return False
    else:
        if confirm is None:
            confirm = lambda: input("Install/update Python requirements in existing .venv? [y/N]: ").strip().lower() in ("y", "yes")
        if not confirm():
            printer("Existing environment left unchanged.")
            return False

    if not py.is_file():
        printer("Virtualenv Python executable missing; no packages installed.")
        return False
    printer("Installing requirements only into .venv (never system Python)...")
    try:
        subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
            cwd=str(root), check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        printer(f"Dependency installation failed ({type(exc).__name__}).")
        printer("Check Python version/wheel support and rerun bootstrap.")
        return False
    printer("")
    printer("Local packages prepared. Next:")
    if not shutil.which("codex"):
        printer("  1. Install the Codex CLI and run: codex login")
    else:
        printer("  1. Authenticate using: codex login (if not already logged in)")
    printer("  2. Run: " + str(py) + " phonedex.py setup --check")
    if not (root / ".env").is_file():
        printer("  3. Run: " + str(py) + " phonedex.py setup")
    else:
        printer("  3. Existing .env untouched. Review missing configuration with setup --check.")
    printer("Setup never places a call. Only --place-call can dial.")
    return True


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="No writes or downloads")
    modes.add_argument("--yes", action="store_true", help="Approve local .venv package setup; not real calls")
    p.add_argument('--dashboard',action='store_true',help='Also offer installation of the optional local dashboard')
    args=p.parse_args(argv)
    if args.check:
        report()
        return 0 if sys.version_info >= MIN_PYTHON else 2
    report()
    if not sys.stdin.isatty() and not args.yes:
        print("Non-interactive session: use --check or --yes (local installation only).")
        return 2
    try:
        ready=prepare(confirm=(lambda: True) if args.yes else None)
        if ready and args.dashboard:
            from install_dashboard import install
            ready=install(confirm=(lambda:True) if args.yes else None)
        return 0 if ready else 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; no calling/deployment attempted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
