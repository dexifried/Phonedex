"""Opt-in local observability around PhoneDex's existing call control.

No SIP, model connection, daemon startup, or remote provider requests here.
The default path returns the original control without touching the filesystem.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

LOG = logging.getLogger('phonedex-dashboard')
TERMINAL = {'ended','completed','failed','cancelled','busy','interrupted','no_answer'}


class _BestEffortEvents:
    """A full/locked disk must never terminate a core-owned phone call."""
    def __init__(self,store):self._inner=store
    def __getattr__(self,name):
        method=getattr(self._inner,name)
        def invoke(*args,**kwargs):
            try:return method(*args,**kwargs)
            except (OSError,sqlite3.Error,RuntimeError,ValueError) as exc:
                LOG.warning('Dashboard event persistence unavailable (%s)',type(exc).__name__)
                return [] if name=='pending_commands' else None
        return invoke


def data_directory() -> Path:
    override = os.environ.get('PHONEDEX_DASHBOARD_DATA_DIR')
    if override:
        return Path(override).expanduser()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'PhoneDex' / 'ControlCentre'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'phonedex' / 'control-centre'


def enabled() -> bool:
    return os.environ.get('PHONEDEX_DASHBOARD_EVENTS') == '1'


def mask_number(number: str) -> str:
    digits = re.sub(r'\D', '', str(number))
    return '••• ••• ' + digits[-4:] if digits else 'Private caller'


def redact_text(text: str, data_dir: Path | None = None) -> str:
    """Fail closed if a configured vault cannot be decrypted for redaction."""
    folder = Path(data_dir) if data_dir else data_directory()
    if not enabled() and data_dir is None:
        return text
    try:
        from .vault import Vault
        cleaned = Vault(folder, passphrase=os.environ.get('PHONEDEX_VAULT_PASSPHRASE')).redact(text)
    except Exception:
        # Never log the exception's message: it could contain a secret value.
        LOG.warning('Vault redaction unavailable; transcript fragment withheld')
        return '[transcript withheld: vault redaction unavailable]'
    cleaned = re.sub(r'(?i)\b(?:bearer\s+|sk-(?:proj-)?)[A-Za-z0-9_.\-]{6,}', '[credential redacted]', cleaned)
    cleaned = re.sub(r'\+[1-9]\d{6,14}', lambda m: mask_number(m.group()), cleaned)
    return cleaned


def instrument(opts, control=None, *, direction='outbound'):
    if not enabled() or isinstance(control, DashboardControl):
        return control
    try:
        return DashboardControl(opts, control, direction=direction)
    except Exception as exc:
        LOG.warning('Local dashboard instrumentation unavailable (%s)', type(exc).__name__)
        return control


class DashboardControl:
    """Compatible with CallControl and TranscriptCapture; forwards real events."""
    def __init__(self, opts, original=None, *, data_dir=None, direction='outbound'):
        from .store import Store
        self.data_dir = Path(data_dir) if data_dir else data_directory()
        self.store = _BestEffortEvents(Store(self.data_dir))
        self.original = original
        self.id = (getattr(original,'id',None) or getattr(opts,'dashboard_call_id',None)
                   or 'call_' + uuid.uuid4().hex)
        self.hangup = getattr(original,'hangup',None) or asyncio.Event()
        self._model = None
        self._closed = False
        self._task = None
        self._start = time.monotonic()
        self._seen = set()
        self._partial={}
        self._submitted={}
        self._state='dialing' if direction=='outbound' else 'answered'
        self._timeline = []
        self.recipient = str(getattr(opts,'to',''))
        self.direction = direction
        self.started = datetime.now(timezone.utc).isoformat()
        self.transcript_enabled = os.environ.get('PHONEDEX_TRANSCRIPT_MODE','off') in {'local','both'}
        self.store.record_call(dict(id=self.id, number_masked=mask_number(self.recipient),
            contact=str(getattr(opts,'dashboard_contact',''))[:160], direction=direction,
            agent_id=getattr(opts,'dashboard_agent_id',None),
            agent_name=getattr(opts,'dashboard_agent_name',getattr(opts,'mode','normal').title()),
            provider=str(getattr(opts,'dashboard_provider','Configured SIP')),
            state='dialing' if direction=='outbound' else 'answered', started_at=self.started,
            transcript_enabled=self.transcript_enabled, controls=[]))
        self.heartbeat()

    @property
    def model(self):
        return getattr(self.original,'model',self._model) if self.original else self._model

    @model.setter
    def model(self,value):
        self._model = value
        if self.original is not None and hasattr(self.original,'model'):
            self.original.model = value

    def heartbeat(self):
        stamp = time.time()
        self.store.set_meta('heartbeat',dict(at=stamp,status='connected',component=self.direction,pid=os.getpid()))
        backend=getattr(self.model,'backend',None)
        controls=[] if self._state!='answered' else ['hangup']
        if controls and getattr(backend,'sideband',None):controls+=['steer','profile_field']
        self.store.record_call(dict(id=self.id,heartbeat_at=stamp,controls=controls))

    def start(self):
        if self._task is None:
            self._task=asyncio.create_task(self._monitor())
        return self

    async def _monitor(self):
        last=0
        while not self._closed:
            try:
                await self.consume_commands()
                for command_id,sent_at in list(self._submitted.items()):
                    if time.monotonic()-sent_at>30:
                        self.store.ack_command(command_id,'failed')
                        del self._submitted[command_id]
                if time.monotonic()-last>=5:
                    self.heartbeat()
                    last=time.monotonic()
            except Exception as exc:
                LOG.warning('Dashboard event bridge unavailable (%s)',type(exc).__name__)
            await asyncio.sleep(.4)

    async def status(self,state,error=None,*,forward=True):
        if forward and self.original is not None and hasattr(self.original,'status'):
            await self.original.status(state,error)
        if self._closed:
            return
        if state not in {'queued','dialing','answered','ending',*TERMINAL}:
            return
        self._state=state
        at=datetime.now(timezone.utc).isoformat()
        event=dict(state=state,at=at,kind='state',call_id=self.id,
                   message='Call '+state.replace('_',' '),source='SIP',level='error' if state=='failed' else 'info')
        self._timeline.append({'state':state,'at':at,'message':event['message']})
        self.store.add_event(event)
        self.store.record_call(dict(id=self.id,state=state,timeline=self._timeline,
                                   duration_seconds=round(time.monotonic()-self._start,1)))

    def on_live_event(self,event):
        if self._closed or not isinstance(event,dict):
            return
        typ=event.get('type','')
        client_id=event.get('client_event_id','')
        if client_id.startswith('cmd_') and client_id[4:] in self._submitted and typ in {'session.instructions.appended','error'}:
            command_id=client_id[4:]
            self.store.ack_command(command_id,'acknowledged' if typ=='session.instructions.appended' else 'failed')
            del self._submitted[command_id]
            return
        if typ in {'session.input_transcript.done','session.output_transcript.done'}:
            side='recipient' if '.input_' in typ else 'agent'
            self._finish_utterance(side)
            return
        allowed={'session.input_transcript.delta','session.output_transcript.delta'}
        if typ in allowed:
            text=event.get('delta')
            if not isinstance(text,str) or not 0<len(text)<=1200:
                return
            event_id=str(event.get('event_id') or uuid.uuid4().hex)[:200]
            if event_id in self._seen:return
            self._seen.add(event_id)
            if len(self._seen)>4096:self._seen.clear()
            side='recipient' if typ.startswith('session.input') else 'agent'
            pending=self._partial.setdefault(side,{'text':'','event':event})
            pending['text']+=text
            pending['event']=event
            if len(pending['text'])>12000:
                pending['text']='[utterance withheld: size limit]'
                self._finish_utterance(side)
            elif event.get('phonedex_completed') is True or event.get('is_final') is True:
                self._finish_utterance(side)
            return
        if self.original is not None:
            self.original.on_live_event(event)
        if typ=='session.started':
            self.store.add_event(dict(call_id=self.id,kind='model',source='GPT Live',level='info',message='Model session connected'))
        elif typ in {'session.delegation.created','session.instructions.appended','error'}:
            # Explicit allowlist: neither tool arguments nor model reasoning.
            self.store.add_event(dict(call_id=self.id,kind='tool' if 'delegation' in typ else 'model',
                source='GPT Live',level='error' if typ=='error' else 'info',
                message={'session.delegation.created':'Business tool requested',
                         'session.instructions.appended':'Private instruction acknowledged',
                         'error':'Model reported an error'}[typ]))

    def _finish_utterance(self,side):
        pending=self._partial.pop(side,None)
        if not pending:return
        event=pending['event']
        text=redact_text(pending['text'],self.data_dir)
        if self.original is not None:self.original.on_live_event({**event,'delta':text})
        if self.transcript_enabled:
            ms=event.get('end_ms',event.get('start_ms',0))
            ms=int(max(0,min(3600000,ms))) if isinstance(ms,(int,float)) else 0
            self.store.transcript(self.id,side,text,ms,str(event.get('event_id') or uuid.uuid4().hex)[:200])

    async def consume_commands(self):
        for command in self.store.pending_commands(self.id):
            try:
                created=datetime.fromisoformat(command['at'].replace('Z','+00:00')).timestamp()
                if time.time()-created>30:
                    self.store.ack_command(command['id'],'rejected')
                    continue
                if command['kind']=='hangup':
                    self.hangup.set()
                    result='completed'
                elif command['kind'] in {'steer','profile_field'}:
                    payload=command['payload']
                    instruction=payload.get('instruction','')
                    if command['kind']=='profile_field':
                        value=self.request_profile_field(payload.get('field_id'),payload.get('purpose'))
                        instruction='Authorized field for this recipient and purpose only: '+payload['purpose']+'. Value: '+value
                    if payload.get('encrypted_instruction'):
                        from .crypto import ProtectedCipher
                        instruction=ProtectedCipher(self.data_dir/'command-keys',os.environ.get('PHONEDEX_VAULT_PASSPHRASE')).decrypt(
                            payload['encrypted_instruction'],'instruction:'+self.id)
                    if not isinstance(instruction,str) or not 2<=len(instruction)<=900:
                        raise ValueError('Invalid instruction')
                    backend=getattr(self.model,'backend',None)
                    if backend is None or getattr(backend,'sideband',None) is None:
                        continue  # Waiting, at most 30 seconds, for the real sideband.
                    await backend.send_private_steer('cmd_'+command['id'],instruction)
                    self._submitted[command['id']]=time.monotonic()
                    result='sent'
                else:
                    result='rejected'
            except Exception:
                result='failed'
            self.store.ack_command(command['id'],result)
            self.store.add_event(dict(call_id=self.id,kind='command',source='Agent actions',
                level='info' if result in {'completed','acknowledged'} else 'warn',
                message=f"{command['kind']} command {result}"))

    def request_profile_field(self,field_id,purpose):
        """Trusted backend tool: scope comes from the actual call, never model args."""
        if self._closed:
            raise PermissionError('Call ended')
        from .vault import Vault
        value=Vault(self.data_dir,passphrase=os.environ.get('PHONEDEX_VAULT_PASSPHRASE')).request_profile_field(
            field_id,self.id,self.recipient,purpose)
        return value

    async def close(self,state='ended'):
        if self._closed:
            return
        for side in list(self._partial):self._finish_utterance(side)
        await self.status(state if state in TERMINAL else 'ended',forward=False)
        self._closed=True
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task,return_exceptions=True)
        self.store.record_call(dict(id=self.id,state=state,ended_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=round(time.monotonic()-self._start,1),controls=[]))


class TranscriptObserver:
    """Inbound adapter retains legacy TranscriptCapture.close/copy behavior."""
    def __init__(self,monitor,capture=None):
        self.monitor,self.capture=monitor,capture
        # All collectors receive only complete, redacted utterances.
        self.monitor.original=capture
    def on_live_event(self,event):
        self.monitor.on_live_event(event)
