"""Explicit, single-use dashboard approvals around the portable SIP dialer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from .integration import mask_number


def config_from(root: Path) -> dict[str,str]:
    """Read the core's inert KEY=VALUE format, without importing its runtime."""
    config={}
    path=Path(root)/'.env'
    if path.is_file() and not path.is_symlink():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key,sep,value=line.strip().partition('=')
            if sep and re.fullmatch(r'PHONEDEX_[A-Z0-9_]+',key.strip()):
                value=value.strip()
                if len(value)>=2 and value[0] in "\"'" and value[-1]==value[0]:
                    value=value[1:-1]
                config[key.strip()]=value
    # Deliberate process environment takes precedence over local config.
    config.update({k:v for k,v in os.environ.items() if k.startswith('PHONEDEX_')})
    return config


class Runtime:
    def __init__(self,data_dir,core_root,store,vault,*,backend=None):
        self.data_dir,self.core_root=Path(data_dir),Path(core_root)
        self.store,self.vault=store,vault
        self.backend=backend
        self._approvals={}
        self._lock=threading.Lock()
        self._child=None
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS call_leases(slot TEXT PRIMARY KEY,call_id TEXT NOT NULL,expires REAL NOT NULL)')

    def _reserve(self,call_id,duration):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            lease=db.execute("SELECT expires FROM call_leases WHERE slot='outbound'").fetchone()
            if lease and lease['expires']>time.time():
                raise ValueError('The outbound call slot is reserved by another dashboard instance')
            db.execute("INSERT OR REPLACE INTO call_leases VALUES('outbound',?,?)",(call_id,time.time()+duration+60))

    def _release(self,call_id):
        with self.store.connect() as db:db.execute('DELETE FROM call_leases WHERE call_id=?',(call_id,))

    def _review(self,payload):
        if self.store.get_meta('demo',False):
            raise ValueError('Dialing is disabled in the synthetic workspace')
        contact=self.store.get('contacts',payload.get('contact_id'))
        agent=self.store.get('agents',payload.get('agent_id'))
        if not contact or not contact.get('allowed'):
            raise ValueError('Select a contact explicitly permitted for calls')
        if not agent or not agent.get('enabled',True):
            raise ValueError('Select an enabled agent')
        number=contact.get('number','')
        config=config_from(self.core_root)
        providers=[p for p in self.store.list('providers') if p.get('enabled') and p.get('kind')!='openai']
        if len(providers)>1:raise ValueError('Enable only one outbound SIP or control provider')
        provider=providers[0] if providers else None
        remote=bool(provider and provider.get('kind')=='worker')
        allowed={n.strip() for n in config.get('PHONEDEX_ALLOWED_RECIPIENTS','').split(',') if n.strip()}
        if not re.fullmatch(r'\+[1-9]\d{6,14}',number) or (not remote and number not in allowed):
            raise ValueError('Recipient must also be in the core PHONEDEX_ALLOWED_RECIPIENTS allowlist')
        duration=payload.get('duration_seconds',120)
        if type(duration) is not int or not 20<=duration<=min(300,agent.get('max_duration_seconds',120)):
            raise ValueError('Duration must be 20–300 seconds and within the agent limit')
        prompt='\n'.join(p for p in (agent.get('prompt',''),payload.get('prompt','')) if p)
        if not prompt.strip() or len(prompt)>2000:
            raise ValueError('Combined agent and task instructions must be 1–2000 characters')
        permissions=payload.get('profile_fields',[])
        if not isinstance(permissions,list) or len(permissions)>20:raise ValueError('Invalid field permissions')
        if remote and permissions:raise ValueError('Profile disclosure is unavailable through this remote control protocol')
        known={f['id']:f for p in self.vault.profiles() for f in self.vault.fields(p['id'])} if permissions else {}
        for grant in permissions:
            if (not isinstance(grant,dict) or set(grant)!={'field_id','purpose'} or grant['field_id'] not in known or
                    not isinstance(grant['purpose'],str) or not 1<=len(grant['purpose'])<=500):
                raise ValueError('Choose an existing field and an exact disclosure purpose')
        mode=agent.get('mode','normal')
        if mode not in {'normal','business'}:
            raise ValueError('Agent mode unsupported by PhoneDex')
        if remote:
            if self.backend is None:raise ValueError('PhoneDex control backend is unavailable')
            configured=provider.get('config',{})
            remote_allowed={v.strip() for v in str(configured.get('allowed_recipients','')).split(',') if v.strip()}
            if remote_allowed and number not in remote_allowed:
                raise ValueError('Recipient is outside the selected provider allowlist')
            # Validate encrypted credentials and endpoint without any network call.
            client=self.backend.client(provider)
            client.close()
            digest=hashlib.sha256(json.dumps([contact,agent,provider,duration,prompt],sort_keys=True).encode()).hexdigest()
            return dict(contact=contact,agent=agent,provider=provider,config={},number=number,duration=duration,
                prompt=prompt,mode=mode,digest=digest,permissions=[],remote=True)
        if provider:
            mapped={'termination_uri':'PHONEDEX_TERMINATION_URI','public_ip':'PHONEDEX_PUBLIC_IP',
                    'caller_id':'PHONEDEX_CALLER_ID','media_port':'PHONEDEX_MEDIA_PORT',
                    'media_port_forwarded':'PHONEDEX_MEDIA_PORT_FORWARDED'}
            for src,target in mapped.items():
                if src in provider.get('config',{}):
                    value=provider['config'][src]
                    config[target]='1' if value is True else '0' if value is False else str(value)
            encrypted=provider.get('credentials_encrypted')
            if encrypted:
                from .crypto import ProtectedCipher
                cipher=ProtectedCipher(self.data_dir/'provider-keys',os.environ.get('PHONEDEX_VAULT_PASSPHRASE'))
                credentials=json.loads(cipher.decrypt(encrypted,'provider:'+provider['id']))
                config['PHONEDEX_SIP_USERNAME']=credentials.get('username','')
                config['PHONEDEX_SIP_PASSWORD']=credentials.get('password','')
        required=('PHONEDEX_TERMINATION_URI','PHONEDEX_PUBLIC_IP','PHONEDEX_CALLER_ID',
                  'PHONEDEX_SIP_USERNAME','PHONEDEX_SIP_PASSWORD')
        if any(not config.get(k) for k in required):
            raise ValueError('Complete the core SIP setup or select an encrypted SIP provider')
        if config.get('PHONEDEX_MEDIA_PORT_FORWARDED')!='1':
            raise ValueError('Manually verify SRTP port forwarding in the core configuration first')
        # Reuse public validators. No carrier connectivity or model request.
        from onboarding import sip_target,public_ipv4,e164
        sip_target(config['PHONEDEX_TERMINATION_URI'])
        public_ipv4(config['PHONEDEX_PUBLIC_IP'])
        e164(config['PHONEDEX_CALLER_ID'])
        try:
            maximum=int(config.get('PHONEDEX_MAX_CALLS_PER_DAY','12'))
        except ValueError:
            raise ValueError('Invalid core daily call limit') from None
        today=datetime.now(timezone.utc).date().isoformat()
        if maximum<1 or sum(str(c.get('started_at','')).startswith(today) and c.get('direction')=='outbound'
                            for c in self.store.calls())>=maximum:
            raise ValueError('Core daily call cap reached')
        digest=hashlib.sha256(json.dumps([contact,agent,provider,config,duration,prompt,permissions],sort_keys=True).encode()).hexdigest()
        return dict(contact=contact,agent=agent,provider=provider,config=config,
                    number=number,duration=duration,prompt=prompt,mode=mode,digest=digest,permissions=permissions)

    def preflight(self,payload):
        with self._lock:
            review=self._review(payload)
            ident=uuid.uuid4().hex
            self._approvals={k:v for k,v in self._approvals.items() if v['expires']>time.time()}
            self._approvals[ident]=dict(payload=dict(payload),digest=review['digest'],expires=time.time()+120)
        return dict(approval_id=ident,expires_at=time.time()+120,
            contact=review['contact']['name'],number_masked=mask_number(review['number']),
            agent=review['agent']['name'],agent_id=review['agent']['id'],
            prompt=review['prompt'],duration_seconds=review['duration'],
            provider=(review['provider'] or {}).get('name','Core SIP configuration'),
            estimated_cost=None,cost_note='Carrier rates are not configured; charges are unknown.',
            authorized_fields=review['permissions'],notice='This explicit approval will place one potentially billable call and create only the reviewed field grants.')

    async def dial(self,approval_id):
        # Dispatch can block on HTTPS. Keep both its lock and I/O off the ASGI
        # event loop so another approval cannot deadlock the dashboard.
        return await asyncio.to_thread(self._dial,approval_id)

    def _dial(self,approval_id):
        with self._lock:
            approval=self._approvals.pop(approval_id,None)
            if not approval or approval['expires']<time.time():
                raise ValueError('Call approval expired or already used; prepare the call again')
            review=self._review(approval['payload'])
            if review['digest']!=approval['digest']:
                raise ValueError('Call configuration changed; prepare the call again')
            if self._child is not None and self._child.poll() is None:
                raise ValueError('One dashboard call is already active')
            fresh=[c for c in self.store.calls() if c.get('state') in {'dialing','answered','active','ending'}
                   and isinstance(c.get('heartbeat_at'),(float,int)) and 0<=time.time()-c['heartbeat_at']<15]
            if fresh:
                raise ValueError('An instrumented PhoneDex call is already active')
            request_id='dashboard_'+uuid.uuid4().hex
            call_id=('call_'+hashlib.sha256(request_id.encode()).hexdigest()[:32] if review.get('remote') else 'call_'+uuid.uuid4().hex)
            self._reserve(call_id,review['duration'])
            if review.get('remote'):
                record=dict(id=call_id,contact=review['contact']['name'],number_masked=mask_number(review['number']),
                    agent_id=review['agent']['id'],agent_name=review['agent']['name'],provider=review['provider']['name'],
                    backend_provider_id=review['provider']['id'],direction='outbound',
                    started_at=datetime.now(timezone.utc).isoformat(),controls=[])
                task=dict(to=review['number'],mode=review['mode'],prompt=review['prompt'],
                    duration_seconds=review['duration'],request_id=request_id)
                # Persist before POST so cancellation/crash can be reconciled by
                # the deterministic ID without submitting another call.
                self.store.record_call({**record,'state':'unknown'})
                try:
                    self.backend.dispatch(review['provider'],task)
                except Exception:
                    # The worker may have accepted the request before the network
                    # failed. Preserve its deterministic ID and reservation; no retry.
                    self.store.record_call({**record,'state':'unknown'})
                    self.store.add_event({'kind':'dispatch_unknown','call_id':call_id,'source':'control-api',
                        'message':'Call submission could not be confirmed. Check this call before trying again.'})
                    raise ValueError('Call submission could not be confirmed; do not automatically retry') from None
                return self.store.record_call({**record,'state':'queued'})
            for grant in review['permissions']:
                self.vault.grant(grant['field_id'],call_id,review['number'],grant['purpose'],min(300,review['duration']+30))
            child_env=os.environ.copy()
            child_env.update(review['config'])
            child_env.update(PHONEDEX_DASHBOARD_EVENTS='1',PHONEDEX_DASHBOARD_DATA_DIR=str(self.data_dir),
                             PHONEDEX_AGENT_HARNESS='1')
            task=dict(call_id=call_id,to=review['number'],duration=review['duration'],mode=review['mode'],
                prompt=review['prompt'],contact=review['contact']['name'],agent_id=review['agent']['id'],
                agent_name=review['agent']['name'],provider=(review['provider'] or {}).get('name','Core SIP configuration'))
            kwargs=dict(cwd=self.core_root,env=child_env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if os.name=='nt':
                kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
            try:child=subprocess.Popen([sys.executable,'-m','controlcentre.worker'],**kwargs)
            except OSError:
                self._release(call_id)
                raise ValueError('Unable to start the approved local call worker') from None
            try:
                child.stdin.write(json.dumps(task).encode('utf-8'))
                child.stdin.close()
            except Exception:
                # Only this newly-created, not-yet-dispatched worker is owned here.
                child.terminate()
                self._release(call_id)
                raise ValueError('Unable to dispatch the approved local task') from None
            self._child=child
            return self.store.record_call(dict(id=call_id,contact=task['contact'],
                number_masked=mask_number(task['to']),agent_id=task['agent_id'],agent_name=task['agent_name'],
                provider=task['provider'],state='dialing',direction='outbound',
                started_at=datetime.now(timezone.utc).isoformat(),controls=[]))

    async def control(self,call_id,kind,payload):
        kind={'instruct':'steer','private_instruction':'steer'}.get(kind,kind)
        if kind not in {'hangup','steer','profile_field'}:
            raise ValueError('This control is unavailable in the portable PhoneDex core')
        call=self.store.get_call(call_id)
        if not call or call.get('state') not in {'dialing','answered','active','ending'}:
            raise ValueError('Call is not active')
        at=call.get('heartbeat_at',0)
        if not isinstance(at,(int,float)) or not 0<=time.time()-at<15 or kind not in call.get('controls',[]):
            raise ValueError('Fresh gateway telemetry and this call capability are required')
        if call.get('backend_provider_id'):
            if kind=='profile_field':raise ValueError('Profile disclosure is unavailable on this backend')
            if kind=='steer':
                instruction=payload.get('instruction','')
                if not isinstance(instruction,str) or not 2<=len(instruction)<=900:
                    raise ValueError('Private instruction must be 2–900 characters')
                payload={'instruction':instruction}
            else:payload={}
            if self.backend is None:raise ValueError('Control backend unavailable')
            return await asyncio.to_thread(self.backend.control,call,kind,payload)
        if kind=='profile_field':
            field_id,purpose=payload.get('field_id'),payload.get('purpose')
            if not isinstance(field_id,str) or not 1<=len(field_id)<=100 or not isinstance(purpose,str) or not 1<=len(purpose)<=500:
                raise ValueError('Choose one field and a specific disclosure purpose')
            payload={'field_id':field_id,'purpose':purpose}
        elif kind=='steer':
            instruction=payload.get('instruction','')
            if not isinstance(instruction,str) or not 2<=len(instruction)<=900:
                raise ValueError('Private instruction must be 2–900 characters')
            # Stored command payload is encrypted: private instructions may contain identifiers.
            from .crypto import ProtectedCipher
            cipher=ProtectedCipher(self.data_dir/'command-keys',os.environ.get('PHONEDEX_VAULT_PASSPHRASE'))
            payload={'encrypted_instruction':cipher.encrypt(instruction,'instruction:'+call_id)}
        else:
            payload={}
        command=self.store.queue_command(call_id,kind,payload)
        return {'id':command['id'],'state':'pending','kind':kind,
                'message':'Queued for the active call; delivery is not yet acknowledged.'}

    async def shutdown(self):
        """Closing the dashboard must not end a call or stop the gateway."""
        self._approvals.clear()
