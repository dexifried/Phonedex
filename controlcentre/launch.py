"""Run only the local dashboard; never start or restart the telephony gateway."""
from __future__ import annotations
import argparse
from contextlib import asynccontextmanager
import ipaddress
from pathlib import Path
import socket
import threading
import time
import webbrowser
from .integration import data_directory


def arguments(argv=None):
    p=argparse.ArgumentParser(description='Optional local PhoneDex Control Centre')
    p.add_argument('--host',default='127.0.0.1',help='Loopback address only')
    p.add_argument('--port',type=int,default=8787)
    p.add_argument('--headless',action='store_true',help='Do not open a browser')
    p.add_argument('--data-dir',type=Path,default=None)
    p.add_argument('--demo',action='store_true',help='Synthetic isolated workspace; dialing disabled')
    return p.parse_args(argv)


def run_dashboard(argv=None,*,core_root=None):
    args=arguments(argv)
    try:loopback=ipaddress.ip_address(args.host).is_loopback
    except ValueError:loopback=False
    if not loopback:raise ValueError('Dashboard binding is restricted to a loopback address')
    if not 0<=args.port<=65535:raise ValueError('Choose a port from 1–65535 (0 selects a free port)')
    folder=(args.data_dir or (data_directory()/'demo' if args.demo else data_directory())).expanduser()
    from .store import Store
    store=Store(folder)
    if args.demo:
        from .fixtures import seed_demo
        seed_demo(store)
    elif store.get_meta('demo',False):
        raise ValueError('Synthetic workspace; use --demo or choose another data directory')
    listener=socket.socket(socket.AF_INET6 if ':' in args.host else socket.AF_INET,socket.SOCK_STREAM)
    try:
        listener.bind((args.host,args.port))
        listener.listen(128)
    except OSError:
        listener.close()
        raise ValueError('Dashboard port is already in use; choose another --port. Existing services were left running.') from None
    actual_port=listener.getsockname()[1]
    try:
        from .app import create_app
        import uvicorn
    except ImportError:
        listener.close()
        raise ValueError('Install optional requirements-dashboard.txt in the core virtualenv first') from None
    root=Path(core_root) if core_root else Path(__file__).resolve().parents[1]
    app=create_app(folder,root,demo=args.demo)
    @asynccontextmanager
    async def lifespan(_app):
        yield
        if app.state.runtime is not None:await app.state.runtime.shutdown()
        with store.connect() as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    app.router.lifespan_context=lifespan
    url=f'http://{("["+args.host+"]") if ":" in args.host else args.host}:{actual_port}'
    server=uvicorn.Server(uvicorn.Config(app,host=args.host,port=actual_port,access_log=False,log_level='warning'))
    if not args.headless:
        def open_when_ready():
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and not server.should_exit:
                if server.started:
                    webbrowser.open(url)
                    return
                time.sleep(.1)
        threading.Thread(target=open_when_ready,daemon=True).start()
    print(f'PhoneDex Control Centre: {url}',flush=True)
    print('Synthetic workspace · dialing disabled' if args.demo else 'Local workspace · create an operator password in your browser',flush=True)
    print('Ctrl+C stops only the dashboard. Existing calls and gateways are unaffected.',flush=True)
    try:server.run(sockets=[listener])
    finally:listener.close()
    return 0


if __name__=='__main__':run_dashboard()
