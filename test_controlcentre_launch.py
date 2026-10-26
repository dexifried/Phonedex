import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from controlcentre.launch import arguments, run_dashboard


def test_loopback_and_headless_defaults():
    args=arguments([])
    assert args.host=='127.0.0.1'
    assert args.port==8787
    assert not args.headless
    assert arguments(['--headless']).headless


def test_public_binding_rejected_before_server(tmp_path):
    with pytest.raises(ValueError,match='loopback'):
        run_dashboard(['--host','0.0.0.0','--headless','--data-dir',str(tmp_path)])


def test_port_conflict_reported_without_touching_other_process(tmp_path):
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    listener.listen()
    port=listener.getsockname()[1]
    try:
        with pytest.raises(ValueError,match='already in use'):
            run_dashboard(['--port',str(port),'--headless','--data-dir',str(tmp_path)])
    finally:
        listener.close()


def test_demo_cannot_reuse_real_workspace(tmp_path):
    from controlcentre.store import Store
    store=Store(tmp_path)
    store.put('contacts',{'name':'Synthetic existing private workspace'})
    with pytest.raises(ValueError,match='empty'):
        run_dashboard(['--demo','--headless','--port','0','--data-dir',str(tmp_path)])
