"""Isolated synthetic browser-test service. Never configures a carrier or calls dial."""
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import uvicorn
from controlcentre.app import create_app
from controlcentre.fixtures import seed_demo

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='phonedex-browser-test-') as temporary:
        app=create_app(Path(temporary),Path(__file__).resolve().parents[2],demo=True)
        seed_demo(app.state.store)
        # A synthetic instrumented session tests advertised UI controls. Demo
        # mode still refuses runtime actions; no transport is configured.
        def heartbeat():
            while True:
                app.state.store.set_meta('heartbeat', {'status':'connected','at':time.time()})
                app.state.store.record_call({'id':'synthetic_browser_live','contact':'Synthetic live session',
                    'number_masked':'••• ••• 0199','agent_name':'Browser test agent','state':'active',
                    'started_at':datetime.now(timezone.utc).isoformat(),'heartbeat_at':time.time(),
                    'controls':['steer','hangup'],'duration_seconds':12,
                    'transcript':[{'event_id':'synthetic_utterance','speaker':'agent','text':'Synthetic test utterance.','ms':500}]})
                time.sleep(5)
        threading.Thread(target=heartbeat,daemon=True).start()
        uvicorn.run(app,host='127.0.0.1',port=8794,log_level='warning')
