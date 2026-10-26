from pathlib import Path
from unittest.mock import patch
from scripts.install_dashboard import install


def test_decline_installs_nothing(tmp_path):
    with patch('scripts.install_dashboard.subprocess.run') as run:
        assert not install(tmp_path,confirm=lambda:False)
        run.assert_not_called()


def test_installer_only_uses_project_venv_and_frontend(tmp_path):
    py=tmp_path/'.venv'/'Scripts'/'python.exe'
    py.parent.mkdir(parents=True)
    py.touch()
    (tmp_path/'dashboard').mkdir()
    with patch('scripts.install_dashboard.shutil.which',return_value='pnpm'),patch('scripts.install_dashboard.subprocess.run') as run:
        assert install(tmp_path,confirm=lambda:True)
        commands=[c.args[0] for c in run.call_args_list]
        assert commands[0][0]==str(py)
        assert '--place-call' not in str(commands)
        assert 'controller' not in str(commands)
