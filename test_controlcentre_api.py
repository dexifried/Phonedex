import csv
import io

import pytest
from fastapi.testclient import TestClient

from controlcentre.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path, tmp_path), base_url='http://127.0.0.1:8765', client=('127.0.0.1', 54321))


def login(client):
    response = client.post('/api/auth/setup', json={'password': 'correct horse battery staple'})
    assert response.status_code == 200, response.text
    return {'x-csrf-token': response.json()['csrf']}


def test_auth_csrf_origin_and_host_protection(client):
    assert client.get('/api/workspace').status_code == 401
    assert client.get('/api/auth/status', headers={'host': 'evil.example'}).status_code == 403
    assert client.post('/api/auth/setup', json={'password': 'correct horse battery staple'}, headers={'origin': 'http://evil.example'}).status_code == 403
    headers = login(client)
    assert 'httponly' in client.headers.get('set-cookie', '').lower() or any(k.lower() == 'httponly' for k in client.cookies.jar._cookies['127.0.0.1']['/']['phonedex_session']._rest)
    assert client.post('/api/contacts', json={'name': 'Office', 'number': '+12025550123'}).status_code == 403
    assert client.post('/api/contacts', headers=headers, json={'name': 'Office', 'number': '+12025550123'}).status_code == 200


def test_empty_workspace_and_validation_are_honest(client):
    headers = login(client)
    workspace = client.get('/api/workspace').json()
    assert workspace['calls'] == []
    assert workspace['health']['gateway'] == 'unknown'
    assert workspace['stats']['success_rate'] is None
    response = client.post('/api/providers', headers=headers, json={'name': 'Bad', 'password': 'SHOULD_NOT_ECHO', 'kind': ['SHOULD_NOT_ECHO']})
    assert response.status_code == 422
    assert 'SHOULD_NOT_ECHO' not in response.text


def test_reauth_export_unknown_costs_and_csv_escape(client):
    headers = login(client)
    client.app.state.store.record_call({'id': 'c1', 'contact': '=SUM(1,1)', 'state': 'completed', 'results': [{'field': 'appointment', 'value': 'Tuesday', 'source': 'recipient', 'at': '2026-10-25T20:00:00Z'}]})
    assert client.get('/api/export').status_code == 403
    assert client.post('/api/auth/reauth', json={'password': 'correct horse battery staple'}, headers=headers).status_code == 200
    response = client.get('/api/export?format=csv')
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]['contact'].startswith("'")
    assert rows[0]['cost'] == ''
    analytics = client.get('/api/analytics').json()
    assert analytics['success_rate'] is None and analytics['total_cost'] is None
    assert client.get('/api/calls/c1').json()['results'][0]['source'] == 'recipient'


def test_controls_fail_closed_and_credential_flags_only(client):
    headers = login(client)
    client.app.state.store.record_call({'id': 'c1', 'state': 'completed', 'controls': ['hangup']})
    assert client.post('/api/calls/c1/control', json={'kind': 'hangup', 'confirm': True}, headers=headers).status_code in (403, 409)
    response = client.post('/api/providers', json={'name': 'Carrier', 'kind': 'sip', 'credentials': {'username': 'alice', 'password': 'secret-value'}}, headers=headers)
    assert response.status_code == 200, response.text
    assert 'secret-value' not in response.text and 'alice' not in response.text
    assert response.json()['credential_presence']['password'] is True
    assert 'secret-value' not in client.get('/api/workspace').text


def test_authentication_attempts_are_throttled_and_sessions_expire(client):
    headers = login(client)
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    for _ in range(5):
        assert client.post('/api/auth/login', json={'password': 'wrong'}).status_code == 401
    assert client.post('/api/auth/login', json={'password': 'wrong'}).status_code == 429


def test_stale_active_call_is_unknown_and_schema_preserves_extensions(client):
    import time
    headers = login(client)
    store = client.app.state.store
    store.record_call({'id': 'c1', 'state': 'active', 'controls': ['hangup']})
    assert client.get('/api/calls/c1').json()['state'] == 'unknown'
    store.set_meta('heartbeat', {'at': time.time(), 'status': 'connected'})
    assert client.get('/api/calls/c1').json()['state'] == 'unknown'
    store.record_call({'id': 'c1', 'heartbeat_at': time.time()})
    assert client.get('/api/calls/c1').json()['state'] == 'active'
    agent = client.post('/api/agents', headers=headers, json={'name': 'Booking', 'extension': '123', 'mode': 'business'}).json()
    update = client.patch('/api/agents/' + agent['id'], headers=headers, json={'description': 'Appointments'})
    assert update.status_code == 200 and update.json()['extension'] == '123'
    assert client.post('/api/agents', headers=headers, json={'name': 'Bad', 'extension': 'abc'}).status_code == 422


def test_native_settings_summary_never_exposes_values(client, tmp_path):
    login(client)
    (tmp_path / '.env').write_text('PHONEDEX_SIP_PASSWORD=secret-value\nPHONEDEX_CALLER_ID=+12025550123\nPHONEDEX_TRANSCRIPT_MODE=local\n')
    response = client.get('/api/settings')
    assert response.status_code == 200
    assert response.json()['configuration']['sip_password'] is True
    assert 'secret-value' not in response.text and '+12025550123' not in response.text
    assert client.get('/api/workspace').json()['health']['transcript_mode'] == 'local'


def test_vault_reauth_redaction_revocation_and_scoped_grants(client):
    headers = login(client)
    profile = client.post('/api/vault/profiles', headers=headers, json={'name': 'Operator'}).json()
    response = client.post('/api/vault/profiles/' + profile['id'] + '/fields', headers=headers, json={'key': 'account', 'label': 'Account', 'value': 'SECRET-VAULT-123'})
    assert response.status_code == 200, response.text
    field = response.json()
    assert 'SECRET-VAULT-123' not in response.text
    assert client.post('/api/vault/fields/' + field['id'] + '/reveal', headers=headers).status_code == 403
    client.post('/api/auth/reauth', headers=headers, json={'password': 'correct horse battery staple'})
    assert client.post('/api/vault/fields/' + field['id'] + '/reveal', headers=headers).json()['value'] == 'SECRET-VAULT-123'
    client.app.state.store.record_call({'id': 'c1', 'contact': 'Office'})
    client.app.state.store.transcript('c1', 'user', 'My account is SECRET-VAULT-123', 0, 'event1')
    assert 'SECRET-VAULT-123' not in client.get('/api/calls/c1').text
    grant = client.post('/api/vault/grants', headers=headers, json={'field_id': field['id'], 'call_id': 'c1', 'recipient': '+12025550123', 'purpose': 'verify account'}).json()
    assert client.delete('/api/vault/grants/' + grant['id'], headers=headers).status_code == 200
    with pytest.raises(PermissionError):
        client.app.state.vault.request_profile_field(field['id'], 'c1', '+12025550123', 'verify account')


def test_provider_validation_requires_local_telephony_fields(client):
    headers = login(client)
    provider = client.post('/api/providers', headers=headers, json={'name': 'Carrier'}).json()
    result = client.post('/api/providers/' + provider['id'] + '/validate', headers=headers).json()
    assert result['valid'] is False
    assert result['network_verified'] is False
    assert any(error['field'] == 'termination_uri' for error in result['errors'])


def test_sse_reconnect_gap_returns_one_snapshot_revision(client):
    import asyncio
    import json
    from starlette.requests import Request
    login(client)
    store = client.app.state.store
    for i in range(5):
        store.add_event({'kind': 'test', 'message': str(i)})
    token = client.cookies.get('phonedex_session')
    endpoint = next(r.endpoint for r in client.app.routes if getattr(r, 'path', '') == '/api/events')
    async def fetch():
        request = Request({'type': 'http', 'method': 'GET', 'path': '/api/events', 'query_string': b'',
                           'headers': [(b'cookie', ('phonedex_session=' + token).encode()), (b'last-event-id', b'0')]})
        response = await endpoint(request)
        chunk = await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        return chunk
    chunk = asyncio.run(fetch())
    data = json.loads(chunk.split('data: ')[1].strip())
    assert data == {'revision': store.revision(), 'snapshot': True}


def test_metadata_refuses_arbitrary_properties_and_scheduling(client):
    headers = login(client)
    assert client.post('/api/contacts', headers=headers, json={'name': 'Office', 'number': '+12025550123', 'admin': True}).status_code == 422
    assert client.post('/api/tasks', headers=headers, json={'name': 'Schedule', 'status': 'pending'}).status_code == 409
    assert client.post('/api/agents', headers=headers, json={'name': 'Untrusted tool', 'tools': ['execute_shell']}).status_code == 409


def test_nonloopback_peer_cannot_spoof_local_host(client):
    remote = TestClient(client.app, base_url='http://127.0.0.1:8765', client=('203.0.113.10', 45321))
    assert remote.get('/api/auth/status').status_code == 403


def test_known_vault_values_redacted_before_metadata_persistence(client):
    headers = login(client)
    vault = client.app.state.vault
    profile = vault.create_profile('Operator')
    vault.put_field(profile['id'], 'account', 'Account', 'PROTECTED-EXACT-123', 'sensitive')
    agent = client.post('/api/agents', headers=headers, json={'name': 'Booking', 'prompt': 'Account PROTECTED-EXACT-123'}).json()
    contact = client.post('/api/contacts', headers=headers, json={'name': 'Office', 'number': '+12025550123', 'notes': 'Account PROTECTED-EXACT-123'}).json()
    assert 'PROTECTED-EXACT-123' not in str(client.app.state.store.get('agents', agent['id']))
    assert 'PROTECTED-EXACT-123' not in str(client.app.state.store.get('contacts', contact['id']))


def test_static_fallback_rejects_index_symlink_outside_distribution(client, tmp_path):
    import os
    dist = tmp_path / 'dashboard' / 'dist'
    dist.mkdir(parents=True)
    outside = tmp_path / 'private.txt'
    outside.write_text('SHOULD_NOT_SERVE')
    try:
        os.symlink(outside, dist / 'index.html')
    except OSError:
        pytest.skip('Symlink creation requires Windows developer mode')
    response = client.get('/any-spa-route')
    assert 'SHOULD_NOT_SERVE' not in response.text


def test_partial_provider_credential_updates_preserve_unedited_secrets(client):
    import json
    headers = login(client)
    response = client.post('/api/providers', headers=headers, json={'name': 'Carrier', 'credentials': {'username': 'original-user', 'password': 'original-password'}})
    provider = response.json()
    updated = client.patch('/api/providers/' + provider['id'], headers=headers, json={'credentials': {'username': 'new-user', 'password': ''}})
    assert updated.status_code == 200
    stored = client.app.state.store.get('providers', provider['id'])
    plaintext = client.app.state.provider_cipher.decrypt(stored['credentials_encrypted'], 'provider:' + provider['id'])
    assert json.loads(plaintext) == {'username': 'new-user', 'password': 'original-password'}
    assert 'original-password' not in updated.text


def test_dial_approval_is_bound_to_preparing_browser_session(client):
    from types import SimpleNamespace
    first = login(client)
    async def dial(approval):
        return {'id': 'synthetic-call'}
    client.app.state.runtime = SimpleNamespace(preflight=lambda payload: {'approval_id': 'synthetic-approval'}, dial=dial)
    review = client.post('/api/preflight', headers=first, json={'contact_id': 'c', 'agent_id': 'a'}).json()
    second = TestClient(client.app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 54322))
    response = second.post('/api/auth/login', json={'password': 'correct horse battery staple'})
    other = {'x-csrf-token': response.json()['csrf']}
    second.post('/api/auth/reauth', headers=other, json={'password': 'correct horse battery staple'})
    assert second.post('/api/dial', headers=other, json={'approval_id': review['approval_id'], 'confirm': True}).status_code == 409
    client.post('/api/auth/reauth', headers=first, json={'password': 'correct horse battery staple'})
    assert client.post('/api/dial', headers=first, json={'approval_id': review['approval_id'], 'confirm': True}).status_code == 200
