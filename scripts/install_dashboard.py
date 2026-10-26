"""Optional local dashboard installation. Never configures or launches telephony."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def install(root=ROOT,*,confirm=None):
    root=Path(root)
    if confirm is None:
        confirm=lambda:input('Install optional dashboard packages into .venv and build the local UI? [y/N]: ').strip().lower() in {'y','yes'}
    if not confirm():return False
    py=root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not py.is_file():
        print('Prepare the project virtualenv first: python scripts/bootstrap.py')
        return False
    manager=shutil.which('pnpm') or shutil.which('pnpm.cmd') or shutil.which('npm') or shutil.which('npm.cmd')
    if not manager:
        print('Node.js 20.19+ and npm or pnpm are needed to build the UI. Install Node.js, then rerun this optional installer.')
        return False
    try:
        subprocess.run([str(py),'-m','pip','install','-r',str(root/'requirements-dashboard.txt')],cwd=root,check=True)
        subprocess.run([manager,'install','--frozen-lockfile'] if 'pnpm' in str(manager).lower() else [manager,'install'],cwd=root/'dashboard',check=True)
        subprocess.run([manager,'run','build'],cwd=root/'dashboard',check=True)
    except (OSError,subprocess.CalledProcessError):
        print('Dashboard installation failed. Review the package-manager error and retry; gateway processes were not changed.')
        return False
    print('Dashboard ready. Launch: '+str(py)+' phonedex.py dashboard')
    return True


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--yes',action='store_true',help='Approve only local optional package installation')
    args=p.parse_args(argv)
    return 0 if install(confirm=(lambda:True) if args.yes else None) else 2


if __name__=='__main__':raise SystemExit(main())
