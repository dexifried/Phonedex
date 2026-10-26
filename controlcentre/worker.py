"""Private stdin dispatcher for a single operator-approved local call."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


async def run(task):
    from .integration import DashboardControl
    from .store import Store
    from sip_outbound import dial
    # Defense in depth; bypassing the web review does not bypass core guards.
    allowed={v.strip() for v in os.environ.get('PHONEDEX_ALLOWED_RECIPIENTS','').split(',')}
    if task['to'] not in allowed or os.environ.get('PHONEDEX_MEDIA_PORT_FORWARDED')!='1':
        raise ValueError('Core calling permissions unavailable')
    opts=SimpleNamespace(to=task['to'],from_number=os.environ['PHONEDEX_CALLER_ID'],
        termination_uri=os.environ['PHONEDEX_TERMINATION_URI'],public_ip=os.environ['PHONEDEX_PUBLIC_IP'],
        media_port=int(os.environ.get('PHONEDEX_MEDIA_PORT','41002')),duration=task['duration'],
        mode=task['mode'],call_prompt=task['prompt'],model_wake_file=os.environ.get('PHONEDEX_MODEL_WAKE_FILE') or None,
        voicemail_mode=os.environ.get('PHONEDEX_VOICEMAIL_MODE','auto'),greeting_file=None,
        dashboard_call_id=task['call_id'],dashboard_contact=task['contact'],
        dashboard_agent_id=task['agent_id'],dashboard_agent_name=task['agent_name'],dashboard_provider=task['provider'])
    control=DashboardControl(opts)
    try:
        await dial(opts,control=control)
    except Exception:
        await control.close('failed')
    finally:
        await control.close('ended')


if __name__=='__main__':
    task=json.loads(sys.stdin.buffer.read(24000))
    try:asyncio.run(run(task))
    except Exception:
        from .integration import data_directory
        from .store import Store
        Store(data_directory()).record_call({'id':task['call_id'],'state':'failed','controls':[]})
    finally:
        from .integration import data_directory
        from .store import Store
        with Store(data_directory()).connect() as db:
            db.execute('DELETE FROM call_leases WHERE call_id=?',(task['call_id'],))
