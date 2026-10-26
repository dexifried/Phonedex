"""Optional adapter to an already-running PhoneDex operator control API.

Only this management process is started/stopped here. No SIP process, carrier
resource or model session is created by discovery or synchronization.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .crypto import ProtectedCipher
from .integration import mask_number
from .operator_client import BackendError, OperatorClient, CALL_ID, COMMAND_ID
from .store import TERMINAL


def epoch(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


class RemoteBackend:
    def __init__(self, store, vault, *, cipher=None, transport=None, index_file=None):
        self.store, self.vault, self.transport = store, vault, transport
        self.cipher = cipher or ProtectedCipher(store.path.parent / 'provider-keys', os.environ.get('PHONEDEX_VAULT_PASSPHRASE'))
        source = index_file or os.environ.get('PHONEDEX_CONTROL_CALL_INDEX')
        self.index_file = Path(source) if source else None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._seeded = set()
        self._polled = {}
        self._retired = set()
        self._transcripts = {}

    def provider(self):
        providers = [p for p in self.store.list('providers') if p.get('enabled') and p.get('kind') != 'openai']
        return providers[0] if len(providers) == 1 and providers[0].get('kind') == 'worker' else None

    def client(self, provider):
        return OperatorClient(provider, self.cipher, transport=self.transport)

    def status(self):
        provider = self.provider()
        if not provider:
            return {'mode': 'local', 'status': 'unconfigured', 'notice': 'No remote control backend selected.'}
        saved = self.store.get_meta('backend_status', {})
        if saved.get('provider_id') != provider['id']:
            saved = {}
        fresh = time.time() - saved.get('last_sync', 0) < 45
        return dict(mode='remote-control', provider_id=provider['id'], provider=provider.get('name', 'PhoneDex'),
            status=saved.get('status', 'connecting') if fresh else 'offline',
            last_sync=saved.get('last_sync'), last_success=saved.get('last_success'),
            error=saved.get('error'), transcript_delivery='Provider batches; may be delayed',
            notice='Connected to PhoneDex control API. Carrier and model connectivity are separate checks.',
            supported_controls=saved.get('supported_controls', []),
            local_transcript_cache=provider.get('config',{}).get('cache_transcripts') is True,
            vault_disclosure=False, polling='5 seconds during calls; 15 seconds while idle')

    def _event(self, call_id, state, at):
        ident = hashlib.sha256(f'{call_id}:{state}:{at}'.encode()).hexdigest()
        if self.store.get('events', ident):
            return
        event = {'id': ident, 'call_id': call_id, 'kind': 'call_state', 'state': state,
                 'at': at, 'message': 'PhoneDex call ' + state.replace('_', ' '), 'source': 'control-api'}
        self.store.add_event(event)
        call = self.store.get_call(call_id)
        timeline = call.get('timeline', [])
        self.store.record_call({'id': call_id, 'timeline': (timeline + [event])[-100:]})

    def _discover(self, client, provider, operations):
        if 'GET /agent/inbound/events' in operations and provider['id'] not in self._seeded:
            data = client.request('GET', '/inbound/events')
            self._incoming(data.get('events', []), provider)
            self._seeded.add(provider['id'])
        if 'GET /agent/events' in operations:
            data = client.request('GET', '/events')
            events = data.get('events', [])
            if not isinstance(events, list) or len(events) > 200:
                raise BackendError('invalid_response')
            self._incoming([v.get('call', {}) for v in events if isinstance(v, dict) and v.get('event') == 'phonedex.inbound_call'], provider)
        if self.index_file:
            try:
                if self.index_file.is_symlink() or self.index_file.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError()
                entries = json.loads(self.index_file.read_text(encoding='utf-8-sig'))
                if not isinstance(entries, dict):
                    raise ValueError()
                for ident, at in sorted(entries.items(), key=lambda x: str(x[1]), reverse=True)[:100]:
                    if CALL_ID.fullmatch(ident) and epoch(at) and time.time() - epoch(at) < 86400:
                        if not self.store.get_call(ident):
                            self.store.record_call({'id': ident, 'direction': 'outbound', 'started_at': at,
                                'backend_provider_id': provider['id'], 'provider': provider['name']})
            except (OSError, ValueError, TypeError):
                raise BackendError('call_index_unavailable') from None

    def _incoming(self, events, provider):
        if not isinstance(events, list) or len(events) > 200:
            raise BackendError('invalid_response')
        for event in sorted((v for v in events if isinstance(v, dict)), key=lambda v: str(v.get('at', ''))):
            ident = event.get('id')
            if not isinstance(ident, str) or not CALL_ID.fullmatch(ident) or not epoch(event.get('at')):
                continue
            state = event.get('state', 'ringing')
            if state not in TERMINAL | {'queued', 'ringing', 'dialing', 'answered', 'active', 'ending'}:
                continue
            previous = self.store.get_call(ident)
            item = dict(id=ident, direction='inbound', state=state, backend_provider_id=provider['id'],
                provider=provider['name'], number_masked=mask_number(event.get('from', '')))
            if not previous:
                item['started_at'] = event['at']
            if state in TERMINAL:
                item['ended_at'] = event['at']
            self.store.record_call(item)
            self._event(ident, state, event['at'])

    def transcript(self,call_id):
        record=self._transcripts.get(call_id)
        return record['items'] if record and record['expires']>time.time() else []

    def _transcript(self, client, call_id, scrub, *, cache=False):
        data = client.request('GET', f'/calls/{call_id}/transcript', missing_ok=True)
        if not data:
            return
        items = data.get('items', [])
        if not isinstance(items, list) or len(items) > 1000:
            raise BackendError('invalid_response')
        valid = [v for v in items if isinstance(v, dict) and v.get('speaker') in {'caller', 'assistant'}
            and isinstance(v.get('text'), str) and 0 < len(v['text']) <= 1200
            and type(v.get('ms', 0)) in {int, float} and 0 <= v.get('ms', 0) <= 36000000]
        # A protected value split across fragments must not evade per-item masking.
        joined = ''.join(v['text'] for v in valid)
        cross_fragment_secret = scrub(joined) != ''.join(scrub(v['text']) for v in valid)
        occurrences = {}
        rendered=[]
        for item in valid:
            signature = json.dumps([item['speaker'], item.get('ms', 0), item['text']])
            occurrence = occurrences.get(signature, 0)
            occurrences[signature] = occurrence + 1
            ident = hashlib.sha256(f'{call_id}:{signature}:{occurrence}'.encode()).hexdigest()
            text = '[Protected profile information redacted]' if cross_fragment_secret else scrub(item['text'])
            speaker='recipient' if item['speaker']=='caller' else 'agent'
            rendered.append({'event_id':ident,'speaker':speaker,'text':text,'ms':int(item.get('ms',0))})
            if cache:
                self.store.transcript(call_id,speaker,text,int(item.get('ms',0)),ident)
        self._transcripts={k:v for k,v in self._transcripts.items() if v['expires']>time.time()}
        if len(self._transcripts)>=100 and call_id not in self._transcripts:
            self._transcripts.pop(next(iter(self._transcripts)))
        changed=self.transcript(call_id)!=rendered
        self._transcripts[call_id]={'items':rendered,'expires':time.time()+900}
        if changed and not cache:
            # Notify SSE clients without storing transcript content.
            with self.store.connect() as db:self.store._change(db)

    def _observe(self, client, provider, call, operations, scrub):
        ident = call['id']
        data = client.request('GET', f'/calls/{ident}', missing_ok=True)
        if not data:
            # A newly submitted KV document can be temporarily invisible.
            if time.time() - (epoch(call.get('started_at')) or 0) > 420:
                self._retired.add(ident)
            return
        if data.get('call_id') != ident:
            raise BackendError('invalid_response')
        state = data.get('state')
        if state not in TERMINAL | {'queued', 'ringing', 'dialing', 'answered', 'active', 'ending'}:
            raise BackendError('invalid_response')
        started = epoch(data.get('created_at'))
        updated = epoch(data.get('updated_at'))
        limit = data.get('duration_seconds', 300)
        limit = limit if type(limit) is int and 20 <= limit <= 300 else 300
        fresh = started is not None and -60 <= time.time() - started <= limit + 120
        controls = []
        if fresh and state in {'answered', 'active'}:
            controls = [kind for kind, operation in [('hangup', 'POST /agent/calls/{id}/hangup'), ('steer', 'POST /agent/calls/{id}/steers')] if operation in operations]
        direction = data.get('direction', call.get('direction', 'outbound'))
        if direction not in {'inbound', 'outbound'}:
            raise BackendError('invalid_response')
        number = data.get('from') if direction == 'inbound' else data.get('to')
        item = dict(id=ident, state=state, direction=direction, controls=controls, heartbeat_at=time.time() if fresh else 0,
            backend_provider_id=provider['id'], provider=provider['name'], number_masked=mask_number(number or ''),
            agent_name=call.get('agent_name') or ('PhoneDex · Business' if data.get('mode') == 'business' else 'PhoneDex · Normal'))
        if started:
            item['started_at'] = data['created_at']
        if state in TERMINAL and updated:
            item['ended_at'] = data['updated_at']
            # The legacy API reports the configured limit, not connected-call
            # duration. Queue/ringing elapsed time is not a carrier duration.
            item['duration_seconds'] = None
        self.store.record_call(item)
        at = data.get('updated_at') or data.get('created_at')
        if epoch(at):
            self._event(ident, state, at)
        if 'GET /agent/calls/{id}/transcript' in operations:
            self._transcript(client, ident, scrub, cache=provider.get('config',{}).get('cache_transcripts') is True)
        if state in TERMINAL:
            if updated and time.time()-updated>180:
                self._retired.add(ident)
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='call_leases'").fetchone():
                    db.execute('DELETE FROM call_leases WHERE call_id=?',(ident,))
        pending = self.store.pending_commands(ident)
        if pending and 'GET /agent/calls/{id}/commands' in operations:
            response = client.request('GET', f'/calls/{ident}/commands', missing_ok=True)
            rows = (response or {}).get('commands', [])
            if not isinstance(rows, list) or len(rows) > 200:
                raise BackendError('invalid_response')
            remote = {v.get('command_id'): v.get('status') for v in rows if isinstance(v, dict)}
            for command in pending:
                state = remote.get(command['payload'].get('remote_command_id'))
                if state in {'acknowledged', 'completed', 'failed'}:
                    self.store.ack_command(command['id'], state)

    def sync_once(self):
        with self._lock:
            expired={k for k,v in self._transcripts.items() if v['expires']<=time.time()}
            for key in expired:self._transcripts.pop(key,None)
            self._retired.difference_update(expired)
            provider = self.provider()
            if not provider:
                return self.status()
            client = None
            old = self.store.get_meta('backend_status', {})
            status = {'provider_id': provider['id'], 'last_sync': time.time(), 'last_success': old.get('last_success')}
            try:
                client = self.client(provider)
                info = client.request('GET')
                operations = info.get('operations')
                if info.get('ok') is not True or not isinstance(operations, list) or len(operations) > 50 or not all(isinstance(v, str) for v in operations):
                    raise BackendError('invalid_response')
                self._discover(client, provider, operations)
                scrub = self.vault.redactor()
                calls = [c for c in self.store.calls() if c.get('backend_provider_id') == provider['id'] and c['id'] not in self._retired]
                def priority(call):
                    active=call['state'] not in TERMINAL and time.time()-(epoch(call.get('started_at')) or 0)<420
                    return (not active,self._polled.get(call['id'],0))
                calls.sort(key=priority)
                for call in calls[:8]:
                    if self._stop.is_set():
                        break
                    self._observe(client, provider, call, operations, scrub)
                    self._polled[call['id']] = time.time()
                status.update(status='connected', error=None, last_sync=time.time(), last_success=time.time(),
                    supported_controls=[kind for kind, op in [('hangup', 'POST /agent/calls/{id}/hangup'), ('steer', 'POST /agent/calls/{id}/steers')] if op in operations])
            except (BackendError, OSError, RuntimeError, ValueError, KeyError, TypeError):
                # Fixed label only: upstream response text may contain secrets/PII.
                import sys
                error = sys.exception()
                status.update(status='offline', error=str(error) if isinstance(error, BackendError) else 'synchronization_unavailable', supported_controls=[])
            finally:
                if client:
                    client.close()
            self.store.set_meta('backend_status', status)
            if status['status'] != old.get('status'):
                self.store.add_event({'kind': 'backend', 'message': 'PhoneDex control API ' + status['status'], 'source': 'control-api'})
            return self.status()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        def work():
            failures = 0
            while not self._stop.is_set():
                try:
                    status = self.sync_once()
                    failures = failures + 1 if status['status'] == 'offline' else 0
                    active = any(c.get('backend_provider_id') and c['state'] not in TERMINAL | {'unknown'} for c in self.store.calls())
                    delay = min(120, 15 * 2 ** min(failures, 3)) if failures else 5 if active else 15
                except Exception:
                    delay = 30
                self._stop.wait(delay)
        self._thread = threading.Thread(target=work, daemon=True, name='phonedex-control-sync')
        self._thread.start()

    async def stop(self):
        self._stop.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join, 10)

    def dispatch(self, provider, task):
        client = self.client(provider)
        try:
            result = client.request('POST', '/calls', task)
            expected = 'call_' + hashlib.sha256(task['request_id'].encode()).hexdigest()[:32]
            if result.get('ok') is not True or result.get('call_id') != expected:
                raise BackendError('dispatch_result_unknown')
            return expected
        finally:
            client.close()

    def control(self, call, kind, payload):
        provider = self.provider()
        if not provider or call.get('backend_provider_id') != provider['id'] or kind not in {'hangup', 'steer'}:
            raise ValueError('Control is unavailable on this backend')
        client = self.client(provider)
        try:
            result = client.request('POST', f"/calls/{call['id']}/{'hangup' if kind == 'hangup' else 'steers'}", payload)
            ident = result.get('command_id')
            if not isinstance(ident, str) or not COMMAND_ID.fullmatch(ident):
                raise BackendError('command_result_unknown')
            command = self.store.queue_command(call['id'], kind, {'remote_command_id': ident})
            return {'id': command['id'], 'kind': kind, 'state': 'pending', 'message': 'Submitted to PhoneDex; delivery is not yet acknowledged.'}
        finally:
            client.close()
