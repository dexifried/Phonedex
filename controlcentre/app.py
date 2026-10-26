"""Optional local dashboard API, with explicit private-subnet TLS mode."""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import os
from pathlib import Path
import time
import threading
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from .analytics import analytics, csv_export
from .auth import Auth, COOKIE
from . import models
from .providers import prepare_provider, public_provider, validate_config
from .store import Store, TERMINAL


def native_summary(core_root):
    """Expose presence flags and an enumerated privacy mode, never .env values."""
    allowed = {'PHONEDEX_TERMINATION_URI': 'termination_uri', 'PHONEDEX_PUBLIC_IP': 'public_ip',
               'PHONEDEX_CALLER_ID': 'caller_id', 'PHONEDEX_SIP_USERNAME': 'sip_username',
               'PHONEDEX_SIP_PASSWORD': 'sip_password', 'PHONEDEX_ALLOWED_RECIPIENTS': 'allowed_recipients',
               'PHONEDEX_GATEWAY_URL': 'gateway_url', 'PHONEDEX_GATEWAY_TOKEN': 'gateway_token',
               'PHONEDEX_MEDIA_PORT_FORWARDED': 'media_port_forwarded', 'PHONEDEX_TRANSCRIPT_MODE': 'transcript_mode'}
    values = {}
    config = Path(core_root) / '.env'
    try:
        if config.is_file() and not config.is_symlink() and config.stat().st_size <= 128 * 1024:
            for line in config.read_text(encoding='utf-8-sig').splitlines():
                key, separator, value = line.partition('=')
                if separator and key.strip() in allowed:
                    values[key.strip()] = value.strip().strip('\"\'')
    except (OSError, UnicodeError):
        pass
    values.update({key: os.environ[key] for key in allowed if key in os.environ})
    mode = values.get('PHONEDEX_TRANSCRIPT_MODE')
    return ({name: bool(values.get(key)) for key, name in allowed.items()},
            mode if mode in {'off', 'local', 'worker', 'both'} else 'unknown')


def create_app(data_dir, core_root, *, demo=False,lan_host=None,lan_subnet=None):
    network=None
    if lan_host is not None:
        from .lan import validate_lan
        _,network=validate_lan(lan_host,lan_subnet)
    from .vault import Vault
    from .crypto import ProtectedCipher
    app = FastAPI(title='PhoneDex Control Centre', docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(Path(data_dir))
    demo = bool(demo or store.get_meta('demo', False))
    auth = Auth(store)
    passphrase = os.environ.get('PHONEDEX_VAULT_PASSPHRASE')
    vault = Vault(Path(data_dir), passphrase=passphrase)
    provider_cipher = ProtectedCipher(Path(data_dir) / 'provider-keys', passphrase=passphrase)
    app.state.store, app.state.auth, app.state.vault = store, auth, vault
    app.state.provider_cipher = provider_cipher
    app.state.demo = demo
    approval_owners = {}
    approval_lock = threading.RLock()
    try:
        from .runtime import Runtime
        app.state.runtime = Runtime(Path(data_dir), Path(core_root), store, vault)
    except ImportError:
        app.state.runtime = None

    @app.middleware('http')
    async def local_boundary(request, call_next):
        reason='invalid_request'
        try:
            navigation=(request.method in {'GET','HEAD'} and request.headers.get('sec-fetch-mode')=='navigate'
                        and request.headers.get('sec-fetch-dest')=='document')
            if request.client:
                peer=ipaddress.ip_address(request.client.host)
                if isinstance(peer,ipaddress.IPv6Address) and peer.ipv4_mapped:peer=peer.ipv4_mapped
                reason='client_outside_allowed_subnet'
                if not peer.is_loopback and (network is None or peer not in network):raise ValueError()
            reason='https_required'
            if network is not None and request.url.scheme!='https':raise ValueError()
            host = request.headers.get('host', '')
            parsed = urlsplit('http://' + host)
            hostname = parsed.hostname
            port = parsed.port
            allowed = hostname in {'localhost','127.0.0.1','::1',lan_host} if network is not None else hostname == 'localhost' or ipaddress.ip_address(hostname or '').is_loopback
            reason='unexpected_host'
            if not allowed or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError()
            origin = request.headers.get('origin')
            if origin and not navigation:
                reason='origin_mismatch'
                origin_url = urlsplit(origin)
                expected_port = port or (443 if request.url.scheme == 'https' else 80)
                origin_port = origin_url.port or (443 if origin_url.scheme == 'https' else 80)
                if (origin_url.scheme != request.url.scheme or origin_url.hostname != hostname or
                        origin_port != expected_port or origin_url.path or origin_url.query or
                        origin_url.fragment or origin_url.username or origin_url.password):
                    raise ValueError()
            reason='cross_site_request'
            if request.headers.get('sec-fetch-site') == 'cross-site' and not navigation:
                raise ValueError()
        except (ValueError, TypeError):
            return JSONResponse({'detail': 'Local same-origin access required','code':reason}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': 'Invalid request fields'}, status_code=422)

    def safe(value):
        scrub=vault.redactor()
        def walk(item):
            if isinstance(item,str):return scrub(item)
            if isinstance(item,dict):return {k:walk(v) for k,v in item.items()}
            if isinstance(item,list):return [walk(v) for v in item]
            return item
        return walk(value)

    def call_view(call,*,sanitize=True):
        if call is None:
            raise HTTPException(404, 'Call not found')
        at = call.get('heartbeat_at')
        fresh = isinstance(at, (int, float)) and 0 <= time.time() - at < 15
        if call['state'] in {'active', 'ringing', 'dialing', 'connected', 'answered', 'ending'} and (not fresh or health()['gateway'] != 'connected'):
            call = {**call, 'state': 'unknown', 'controls': []}
        return safe(call) if sanitize else call

    def session_json(session):
        return dict(authenticated=True, csrf=session['csrf'], reauth_until=session['reauth_until'])

    def issue(response):
        token, session = auth.issue()
        response.set_cookie(COOKIE, token, httponly=True, secure=network is not None, samesite='strict', max_age=8 * 3600, path='/')
        return session_json(session)

    @app.get('/api/auth/status')
    def auth_status(request: Request):
        try:
            auth.session(request)
            authenticated = True
        except HTTPException:
            authenticated = False
        configured=auth.configured()
        remote_setup=not configured and network is not None and request.client and not ipaddress.ip_address(request.client.host).is_loopback
        return dict(configured=configured, authenticated=authenticated,setup_required=not configured,pairing_required=bool(remote_setup))

    @app.post('/api/auth/pairing')
    def pairing(request:Request):
        if not request.client or not ipaddress.ip_address(request.client.host).is_loopback:
            raise HTTPException(403,'Pairing links can only be issued on this computer')
        auth.throttle('pairing')
        return {'pairing_token':auth.create_pairing(),'expires_in':600}

    @app.get('/api/auth/session')
    def auth_session(request: Request):
        return session_json(auth.require(request))

    @app.post('/api/auth/setup')
    def setup(request:Request,payload: models.Setup, response: Response):
        auth.throttle('setup')
        remote=network is not None and request.client and not ipaddress.ip_address(request.client.host).is_loopback
        auth.setup(payload.password,pairing_token=payload.pairing_token,remote=bool(remote))
        return issue(response)

    @app.post('/api/auth/login')
    def login(payload: models.Password, response: Response):
        auth.throttle('login')
        if not auth.verify(payload.password):
            raise HTTPException(401, 'Invalid operator password')
        return issue(response)

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response):
        auth.require(request)
        auth.sessions.pop(request.cookies.get(COOKIE), None)
        response.delete_cookie(COOKIE, path='/')
        return {'authenticated': False}

    @app.post('/api/auth/reauth')
    def reauth(request: Request, payload: models.Password):
        session = auth.require(request)
        auth.throttle('reauth')
        if not auth.verify(payload.password):
            raise HTTPException(401, 'Invalid operator password')
        session['reauth_until'] = time.time() + 120
        return session_json(session)

    def health():
        heartbeat = store.get_meta('heartbeat') or {}
        at = heartbeat.get('at')
        fresh = isinstance(at, (int, float)) and 0 <= time.time() - at < 15
        _, mode = native_summary(core_root)
        return dict(dashboard='online', gateway='connected' if fresh and heartbeat.get('status') == 'connected' else 'unknown',
                    gateway_last_seen=at, vault='available', demo=demo, transcript_mode=mode,
                    transcript_storage=None if mode == 'unknown' else mode in {'local', 'both'},
                    sip_transport='Unverified', gpt_live='Unverified')

    @app.get('/api/workspace')
    def workspace(request: Request):
        auth.require(request)
        calls = [call_view(c,sanitize=False) for c in store.calls()]
        connection = health()
        return safe(dict(health=connection, stats=analytics(calls), calls=calls,
                    agents=store.list('agents'), contacts=store.list('contacts'),
                    providers=[public_provider(p) for p in store.list('providers')], tasks=store.list('tasks'),
                    logs=store.events(), activity=store.events(30),
                    capabilities=dict(dial=not demo and app.state.runtime is not None, hangup=True,
                                      private_instruction=True, hold=False, mute=False, transfer=False,
                                      conference=False, scheduling=False, messaging=False)))

    @app.get('/api/calls/{call_id}')
    def get_call(request: Request, call_id: str):
        auth.require(request)
        call = call_view(store.get_call(call_id))
        return call

    @app.get('/api/analytics')
    def get_analytics(request: Request, days: int = 30):
        auth.require(request)
        if not 1 <= days <= 3660:
            raise HTTPException(422, 'Invalid analytics period')
        return analytics([call_view(call) for call in store.calls()], days)

    @app.get('/api/settings')
    def settings(request: Request):
        auth.require(request)
        presence, mode = native_summary(core_root)
        return dict(configuration=presence, mode='local', demo=demo,
                    gateway_managed=False, transcript_storage='opt-in', transcript_mode=mode,
                    unavailable=['hold', 'mute', 'transfer', 'conference', 'scheduled_dialing', 'messaging'])

    @app.get('/api/export')
    def export(request: Request, format: str = 'json'):
        auth.require(request, fresh=True)
        calls = safe(store.calls())
        if format == 'json':
            return JSONResponse(calls, headers={'Content-Disposition': 'attachment; filename="phonedex-calls.json"'})
        if format == 'csv':
            return Response(csv_export(calls), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="phonedex-calls.csv"'})
        raise HTTPException(422, 'Unsupported export format')

    @app.get('/api/calls/{call_id}/export')
    def export_call(request: Request, call_id: str):
        auth.require(request, fresh=True)
        return JSONResponse(call_view(store.get_call(call_id)), headers={'Content-Disposition': 'attachment; filename="phonedex-call.json"'})

    registry = {'agents': models.Agent, 'contacts': models.Contact, 'providers': models.Provider, 'tasks': models.Task}

    def kind_model(kind):
        if kind not in registry:
            raise HTTPException(404, 'Collection not found')
        return registry[kind]

    def save_item(kind, raw, object_id=None):
        model = kind_model(kind)
        existing = store.get(kind, object_id) if object_id else None
        if object_id and existing is None:
            raise HTTPException(404, 'Item not found')
        previous = {k: v for k, v in (existing or {}).items() if k in model.model_fields}
        try:
            item = model.model_validate({**previous, **raw}).model_dump()
        except ValidationError:
            raise HTTPException(422, 'Invalid request fields') from None
        if kind == 'tasks' and item['status'] == 'pending':
            raise HTTPException(409, 'Scheduled tasks are unavailable; save a draft')
        if kind == 'agents' and item['tools']:
            raise HTTPException(409, 'Arbitrary agent tools are unavailable')
        credentials = item.pop('credentials', None) if kind == 'providers' else None
        cleaned = safe(item)
        if kind == 'contacts' and cleaned['number'] != item['number']:
            raise HTTPException(409, 'Contact number cannot be stored while protected or redaction is unavailable')
        if kind == 'providers' and cleaned['config'] != item['config']:
            raise HTTPException(409, 'Provider configuration cannot be stored while protected or redaction is unavailable')
        item = cleaned
        if credentials is not None:
            item['credentials'] = credentials
        item['id'] = object_id or uuid.uuid4().hex
        if kind == 'providers':
            item = prepare_provider(item, provider_cipher, existing)
        saved = store.put(kind, item, item['id'])
        store.add_event({'kind': 'configuration', 'message': f'{kind} saved', 'source': 'operator'})
        return public_provider(saved) if kind == 'providers' else safe(saved)

    @app.get('/api/{kind}')
    def list_items(request: Request, kind: str):
        auth.require(request)
        kind_model(kind)
        return [public_provider(i) for i in store.list(kind)] if kind == 'providers' else safe(store.list(kind))

    @app.post('/api/{kind}')
    def create_item(request: Request, kind: str, payload: dict):
        auth.require(request)
        return save_item(kind, payload)

    @app.patch('/api/{kind}/{object_id}')
    def update_item(request: Request, kind: str, object_id: str, payload: dict):
        auth.require(request)
        return save_item(kind, payload, object_id)

    @app.delete('/api/{kind}/{object_id}')
    def delete_item(request: Request, kind: str, object_id: str):
        auth.require(request)
        kind_model(kind)
        if not store.delete(kind, object_id):
            raise HTTPException(404, 'Item not found')
        return {'deleted': True}

    @app.post('/api/providers/{provider_id}/validate')
    def validate_provider(request: Request, provider_id: str):
        auth.require(request)
        provider = store.get('providers', provider_id)
        if not provider:
            raise HTTPException(404, 'Provider not found')
        result = validate_config(provider.get('config', {}), provider.get('kind', 'sip'))
        provider['validation'] = result
        store.put('providers', provider, provider_id)
        return result

    async def runtime_call(method, *args):
        runtime = app.state.runtime
        if demo or runtime is None or not hasattr(runtime, method):
            raise HTTPException(501, 'Runtime capability unavailable')
        try:
            result = getattr(runtime, method)(*args)
            return safe(await result if inspect.isawaitable(result) else result)
        except (ValueError, RuntimeError, PermissionError, KeyError, OSError):
            raise HTTPException(409, 'Runtime prerequisites or approval not satisfied') from None

    @app.post('/api/preflight')
    async def preflight(request: Request, payload: models.Preflight):
        session = auth.require(request)
        result = await runtime_call('preflight', payload.model_dump())
        with approval_lock:
            for key in list(approval_owners):
                if approval_owners[key]['expires'] <= time.time():
                    approval_owners.pop(key)
            if len(approval_owners) >= 128:
                approval_owners.pop(next(iter(approval_owners)))
            approval_owners[result['approval_id']] = dict(csrf=session['csrf'], expires=time.time() + 120)
        return result

    @app.post('/api/dial')
    async def dial(request: Request, payload: models.Dial):
        session = auth.require(request, fresh=True)
        if not payload.confirm:
            raise HTTPException(409, 'Explicit call confirmation required')
        with approval_lock:
            owner = approval_owners.get(payload.approval_id)
            if not owner or owner['expires'] <= time.time() or owner['csrf'] != session['csrf']:
                raise HTTPException(409, 'Prepare a fresh call approval in this session')
            approval_owners.pop(payload.approval_id)
        return await runtime_call('dial', payload.approval_id)

    @app.post('/api/calls/{call_id}/control')
    async def control(request: Request, call_id: str, payload: models.Control):
        auth.require(request, fresh=True)
        call = store.get_call(call_id)
        if not call:
            raise HTTPException(404, 'Call not found')
        if payload.kind not in {'hangup', 'instruct', 'steer', 'private_instruction','profile_field'}:
            raise HTTPException(501, 'Call control unavailable')
        kind = 'steer' if payload.kind in {'private_instruction', 'instruct'} else payload.kind
        controls = call.get('controls') or []
        if (health()['gateway'] != 'connected' or call_view(call)['state'] == 'unknown' or
                call['state'] not in {'active', 'connected', 'answered', 'ringing', 'dialing', 'ending'} or
                (kind not in controls and payload.kind not in controls)):
            raise HTTPException(409, 'Call control is not currently available')
        if not payload.confirm:
            raise HTTPException(409, 'Explicit control confirmation required')
        if kind == 'steer' and (set(payload.payload) - {'instruction', 'text'} or not isinstance(payload.payload.get('instruction', payload.payload.get('text')), str)):
            raise HTTPException(422, 'Invalid instruction payload')
        command_payload = dict(payload.payload)
        if kind == 'steer':
            command_payload = {'instruction': command_payload.get('instruction', command_payload.get('text', ''))}
        return await runtime_call('control', call_id, kind, command_payload)

    def vault_operation(method, *args):
        try:
            return getattr(vault, method)(*args)
        except KeyError:
            raise HTTPException(404, 'Vault item not found') from None
        except PermissionError:
            raise HTTPException(403, 'Vault permission denied') from None
        except (ValueError, RuntimeError):
            raise HTTPException(409, 'Vault is locked or operation is unavailable') from None

    @app.get('/api/vault/profiles')
    def profiles(request: Request):
        auth.require(request)
        return vault_operation('profiles')

    @app.post('/api/vault/profiles')
    def create_profile(request: Request, payload: models.Profile):
        auth.require(request)
        return vault_operation('create_profile', payload.name)

    @app.delete('/api/vault/profiles/{profile_id}')
    def delete_profile(request: Request, profile_id: str):
        auth.require(request, fresh=True)
        vault_operation('delete_profile', profile_id)
        return {'deleted': True}

    @app.get('/api/vault/profiles/{profile_id}/fields')
    def fields(request: Request, profile_id: str):
        auth.require(request)
        return vault_operation('fields', profile_id)

    @app.post('/api/vault/profiles/{profile_id}/fields')
    def put_field(request: Request, profile_id: str, payload: models.VaultField):
        auth.require(request)
        return vault_operation('put_field', profile_id, payload.key, payload.label, payload.value, payload.sensitivity)

    @app.post('/api/vault/fields/{field_id}/reveal')
    def reveal(request: Request, field_id: str):
        auth.require(request, fresh=True)
        return {'value': vault_operation('reveal', field_id)}

    @app.delete('/api/vault/fields/{field_id}')
    def delete_field(request: Request, field_id: str):
        auth.require(request, fresh=True)
        vault_operation('delete_field', field_id)
        return {'deleted': True}

    @app.get('/api/vault/grants')
    def grants(request: Request):
        auth.require(request)
        return vault_operation('grants')

    @app.post('/api/vault/grants')
    def grant(request: Request, payload: models.Grant):
        auth.require(request, fresh=True)
        if not store.get_call(payload.call_id):
            raise HTTPException(404, 'Call not found')
        return vault_operation('grant', payload.field_id, payload.call_id, payload.recipient, payload.purpose, payload.expires_seconds)

    @app.delete('/api/vault/grants/{grant_id}')
    def revoke(request: Request, grant_id: str):
        auth.require(request)
        vault_operation('revoke', grant_id)
        return {'revoked': True}

    @app.get('/api/vault/audit')
    def audit(request: Request):
        auth.require(request)
        return vault_operation('audit')

    @app.delete('/api/vault')
    def delete_vault(request: Request):
        auth.require(request, fresh=True)
        vault_operation('delete_all')
        return {'deleted': True}

    @app.get('/api/events')
    async def events(request: Request):
        auth.require(request)
        try:
            last = int(request.headers.get('last-event-id', request.query_params.get('since', '0')))
            if last < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Invalid event cursor') from None

        async def generate():
            nonlocal last
            current = store.revision()
            if last != current:
                yield f'id: {current}\nevent: change\ndata: {json.dumps({"revision": current, "snapshot": True})}\n\n'
                last = current
            while not await request.is_disconnected():
                if not auth.sessions.get(request.cookies.get(COOKIE), {}).get('expires', 0) > time.time():
                    break
                changes = store.changes_since(last)
                if changes:
                    current = changes[-1]['revision']
                    data = {'revision': current}
                    if changes[0]['revision'] > last + 1:
                        data['snapshot'] = True
                    yield f'id: {current}\nevent: change\ndata: {json.dumps(data)}\n\n'
                    last = current
                else:
                    yield ': keepalive\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    # Literal API paths must take precedence over generic metadata paths above.
    app.router.routes.sort(key=lambda r: ('{kind}' in getattr(r, 'path', ''),))
    dist = Path(core_root) / 'dashboard' / 'dist'

    @app.get('/{path:path}')
    def frontend(path: str):
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404, 'API route not found')
        root = dist.resolve()
        target = (dist / path).resolve()
        if not root.is_relative_to(Path(core_root).resolve()) or not target.is_relative_to(root):
            raise HTTPException(404, 'File not found')
        if target.is_file():
            return FileResponse(target)
        index = (dist / 'index.html').resolve()
        if index.is_relative_to(root) and index.is_file():
            return FileResponse(index)
        return JSONResponse({'detail': 'Build dashboard frontend to display the control centre'}, status_code=503)

    return app
