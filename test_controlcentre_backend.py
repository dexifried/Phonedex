"""Synthetic control-API integration; no carrier resources or real calls."""
import asyncio
import hashlib
import json
import time
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from controlcentre.crypto import ProtectedCipher
from controlcentre.store import Store
from controlcentre.vault import Vault

CALL='call_'+'a'*32


@pytest.fixture
def connected(tmp_path):
    from controlcentre.backend import RemoteBackend
    store=Store(tmp_path)
    vault=Vault(tmp_path,passphrase='synthetic-encryption-passphrase')
    cipher=ProtectedCipher(tmp_path/'provider-keys',passphrase='synthetic-encryption-passphrase')
    secret='synthetic-operator-token-'+'z'*32
    provider=store.put('providers',{'id':'relay','name':'Synthetic PhoneDex','kind':'worker','enabled':True,
        'config':{'gateway_url':'https://synthetic.invalid/agent/gateway'},
        'credentials_encrypted':cipher.encrypt(json.dumps({'token':secret}),'provider:relay')})
    requests=[]
    started=datetime.now(timezone.utc).isoformat()
    def handler(request):
        requests.append(request)
        assert request.headers['authorization']=='Bearer '+secret
        path=request.url.path
        if path=='/agent':return httpx.Response(200,json={'ok':True,'operations':[
            'POST /agent/calls','GET /agent/calls/{id}','GET /agent/calls/{id}/transcript',
            'GET /agent/calls/{id}/commands','POST /agent/calls/{id}/hangup',
            'POST /agent/calls/{id}/steers','GET /agent/inbound/events','GET /agent/events']})
        if path=='/agent/inbound/events':return httpx.Response(200,json={'events':[{'id':CALL,'state':'ringing','direction':'inbound','from':'+12025550123','at':started}]})
        if path=='/agent/events':return httpx.Response(200,json={'events':[]})
        if path==f'/agent/calls/{CALL}':return httpx.Response(200,json={'call_id':CALL,'state':'answered','direction':'inbound','from':'+12025550123','mode':'normal','created_at':started,'updated_at':started,'duration_seconds':120})
        if path.endswith('/transcript'):return httpx.Response(200,json={'call_id':CALL,'items':[{'speaker':'caller','text':'My identifier is SYNTHETIC-SECRET-ID.','ms':10},{'speaker':'assistant','text':'Thank you.','ms':20}]})
        if path.endswith('/commands'):return httpx.Response(200,json={'commands':[]})
        return httpx.Response(404,json={'error':'unknown_or_expired_call'})
    backend=RemoteBackend(store,vault,cipher=cipher,transport=httpx.MockTransport(handler))
    return backend,store,vault,requests,provider


def test_sync_real_protocol_masking_transcripts_and_deduplication(connected):
    backend,store,vault,requests,provider=connected
    provider['config']['cache_transcripts']=True
    store.put('providers',provider,provider['id'])
    profile=vault.create_profile('Synthetic profile')
    vault.put_field(profile['id'],'other_id','Synthetic ID','SYNTHETIC-SECRET-ID','sensitive')
    backend.sync_once()
    call=store.get_call(CALL)
    assert call['state']=='answered'
    assert call['direction']=='inbound'
    assert call['backend_provider_id']=='relay'
    assert '+12025550123' not in json.dumps(call)
    assert 'SYNTHETIC-SECRET-ID' not in json.dumps(call)
    assert call['controls']==['hangup','steer']
    assert len(call['transcript'])==2
    assert call['task_success'] is None and call['cost'] is None
    backend.sync_once()
    assert len(store.get_call(CALL)['transcript'])==2
    assert backend.status()['status']=='connected'
    assert all(r.method=='GET' for r in requests)


def test_backend_failure_preserves_cached_history_and_hides_server_error(connected):
    backend,store,_,_,_=connected
    backend.sync_once()
    backend.transport=httpx.MockTransport(lambda request:httpx.Response(401,json={'error':'DO-NOT-ECHO-SECRET'}))
    backend.sync_once()
    assert backend.status()['status']=='offline'
    assert store.get_call(CALL) is not None
    assert 'DO-NOT-ECHO-SECRET' not in json.dumps(backend.status())


def test_remote_credentials_never_follow_redirects(connected):
    backend,store,_,_,provider=connected
    seen=[]
    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302,headers={'location':'https://other.invalid/steal'})
    backend.transport=httpx.MockTransport(redirect)
    backend.sync_once()
    assert backend.status()['status']=='offline'
    assert len(seen)==1
    assert all('other.invalid' not in url for url in seen)


def test_stale_remote_call_never_advertises_controls(connected):
    backend,store,_,_,_=connected
    backend.sync_once()
    old='2020-01-01T00:00:00+00:00'
    original=backend.transport
    def stale(request):
        response=original.handle_request(request)
        if request.url.path==f'/agent/calls/{CALL}':
            data=json.loads(response.content);data.update(created_at=old,updated_at=old)
            return httpx.Response(200,json=data)
        return response
    backend.transport=httpx.MockTransport(stale)
    backend.sync_once()
    assert store.get_call(CALL)['controls']==[]


def test_local_outbound_index_discovers_calls_without_reading_private_runtime_code(connected,tmp_path):
    backend,store,_,_,_=connected
    index=tmp_path/'dispatched.json'
    index.write_text(json.dumps({CALL:datetime.now(timezone.utc).isoformat(),'not-a-call':'invalid'}))
    backend.index_file=index
    backend.sync_once()
    assert store.get_call(CALL) is not None


def test_remote_preflight_and_single_use_dispatch_never_launch_local_sip(connected,tmp_path):
    from controlcentre.runtime import Runtime
    backend,store,vault,requests,_=connected
    store.put('contacts',{'id':'contact','name':'Synthetic contact','number':'+12025550125','allowed':True})
    store.put('agents',{'id':'agent','name':'Synthetic agent','enabled':True,'mode':'normal','prompt':'Be brief.','max_duration_seconds':120})
    runtime=Runtime(tmp_path,tmp_path,store,vault,backend=backend)
    original=backend.transport
    def dispatch(request):
        if request.method=='POST' and request.url.path=='/agent/calls':
            requests.append(request)
            body=json.loads(request.content)
            call_id='call_'+hashlib.sha256(body['request_id'].encode()).hexdigest()[:32]
            return httpx.Response(202,json={'ok':True,'call_id':call_id,'state':'queued'})
        return original.handle_request(request)
    backend.transport=httpx.MockTransport(dispatch)
    with patch('controlcentre.runtime.subprocess.Popen',side_effect=AssertionError('local SIP must not start')):
        review=runtime.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask the opening hours.'})
        assert not requests
        call=asyncio.run(runtime.dial(review['approval_id']))
        assert call['state']=='queued'
        assert call['backend_provider_id']=='relay'
        with pytest.raises(ValueError):asyncio.run(runtime.dial(review['approval_id']))
    sent=[r for r in requests if r.method=='POST']
    assert len(sent)==1 and json.loads(sent[0].content)['to']=='+12025550125'


def test_remote_profile_disclosure_fails_closed(connected,tmp_path):
    from controlcentre.runtime import Runtime
    backend,store,vault,_,_=connected
    profile=vault.create_profile('Synthetic')
    field=vault.put_field(profile['id'],'other_id','ID','SYNTHETIC-ID','sensitive')
    store.put('contacts',{'id':'contact','name':'Synthetic','number':'+12025550125','allowed':True})
    store.put('agents',{'id':'agent','name':'Synthetic','enabled':True,'prompt':'Be brief.','mode':'normal','max_duration_seconds':120})
    runtime=Runtime(tmp_path,tmp_path,store,vault,backend=backend)
    with pytest.raises(ValueError,match='[Pp]rofile'):
        runtime.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask hours.',
            'profile_fields':[{'field_id':field['id'],'purpose':'Verify membership'}]})


def test_authenticated_backend_api_reports_real_sync_and_keeps_secrets_private(tmp_path):
    from fastapi.testclient import TestClient
    from controlcentre.app import create_app
    app=create_app(tmp_path,tmp_path)
    client=TestClient(app,base_url='http://127.0.0.1:8787',client=('127.0.0.1',1234))
    assert client.get('/api/backend').status_code==401
    setup=client.post('/api/auth/setup',json={'password':'synthetic-backend-password'})
    headers={'x-csrf-token':setup.json()['csrf']}
    assert client.post('/api/backend/sync').status_code==403
    saved=client.post('/api/providers',headers=headers,json={'name':'Synthetic relay','kind':'worker','enabled':True,
        'config':{'gateway_url':'https://synthetic.invalid/agent/gateway'},'credentials':{'token':'synthetic-token-'+'q'*40}})
    assert saved.status_code==200
    app.state.backend.transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ok':True,'operations':[]}))
    result=client.post('/api/backend/sync',headers=headers)
    assert result.status_code==200
    assert result.json()['status']=='connected'
    workspace=client.get('/api/workspace')
    assert workspace.json()['health']['backend']['status']=='connected'
    assert workspace.json()['health']['gpt_live']=='Unverified'
    assert 'synthetic-token-' not in workspace.text+result.text


def test_ambiguous_remote_dispatch_is_not_retried_and_retains_call_reservation(connected,tmp_path):
    from controlcentre.runtime import Runtime
    backend,store,vault,requests,_=connected
    store.put('contacts',{'id':'contact','name':'Synthetic','number':'+12025550125','allowed':True})
    store.put('agents',{'id':'agent','name':'Synthetic','enabled':True,'prompt':'Be brief.','mode':'normal','max_duration_seconds':120})
    runtime=Runtime(tmp_path,tmp_path,store,vault,backend=backend)
    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout('synthetic upstream error with secret text')
    backend.transport=httpx.MockTransport(fail)
    review=runtime.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask hours.'})
    with pytest.raises(ValueError,match='could not be confirmed'):
        asyncio.run(runtime.dial(review['approval_id']))
    assert len(requests)==1
    call=store.calls()[0]
    assert call['state']=='unknown'
    assert 'secret text' not in json.dumps(call)
    second=runtime.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask hours.'})
    with pytest.raises(ValueError,match='reserved'):asyncio.run(runtime.dial(second['approval_id']))
    assert len(requests)==1


def test_backend_thread_stops_without_stopping_any_gateway(connected):
    backend,_,_,_,_=connected
    backend.start()
    asyncio.run(backend.stop())
    assert not backend._thread.is_alive()


def test_remote_transcripts_are_memory_only_without_explicit_local_cache_opt_in(connected):
    backend,store,_,_,_=connected
    backend.sync_once()
    assert store.get_call(CALL)['transcript']==[]
    assert len(backend.transcript(CALL))==2


def test_transcript_redaction_handles_complete_and_split_secrets_in_same_batch(connected):
    backend,store,vault,_,_=connected
    profile=vault.create_profile('Synthetic')
    vault.put_field(profile['id'],'other_id','Complete','SYNTHETIC-SECRET-ID','sensitive')
    vault.put_field(profile['id'],'other_id_2','Split','SPLIT-SECRET-IDENTIFIER','sensitive')
    original=backend.transport
    def split(request):
        if request.url.path.endswith('/transcript'):
            return httpx.Response(200,json={'items':[{'speaker':'caller','text':text,'ms':i} for i,text in enumerate([
                'SYNTHETIC-SECRET-ID','SPLIT-SECRET-','IDENTIFIER'])]})
        return original.handle_request(request)
    backend.transport=httpx.MockTransport(split)
    backend.sync_once()
    text=''.join(v['text'] for v in backend.transcript(CALL))
    assert 'SPLIT-SECRET-IDENTIFIER' not in text
    assert 'SYNTHETIC-SECRET-ID' not in text


def test_active_calls_are_polled_before_old_history(connected):
    backend,store,_,requests,_=connected
    backend.sync_once()
    backend._polled[CALL]=time.time()
    for i in range(40):
        ident='call_'+f'{i:032x}'
        store.record_call({'id':ident,'state':'ended','backend_provider_id':'relay','started_at':'2020-01-01T00:00:00+00:00'})
    requests.clear()
    backend.sync_once()
    assert any(r.url.path==f'/agent/calls/{CALL}' for r in requests)


def test_final_transcripts_are_reconciled_beyond_three_early_empty_polls(connected,monkeypatch):
    backend,_,_,_,_=connected
    original=backend.transport
    polls=0
    started=time.time()
    def delayed(request):
        nonlocal polls
        response=original.handle_request(request)
        if request.url.path==f'/agent/calls/{CALL}':
            data=json.loads(response.content);data['state']='ended'
            return httpx.Response(200,json=data)
        if request.url.path.endswith('/transcript'):
            polls+=1
            if polls<5:return httpx.Response(200,json={'items':[]})
        return response
    backend.transport=httpx.MockTransport(delayed)
    for i in range(6):
        monkeypatch.setattr('controlcentre.backend.time.time',lambda:started+i*5)
        backend.sync_once()
    assert len(backend.transcript(CALL))==2
    assert backend.store.get_call(CALL)['duration_seconds'] is None


def test_concurrent_remote_approvals_cannot_deadlock_dashboard(tmp_path):
    import subprocess,sys,textwrap
    script=textwrap.dedent('''
        import asyncio,hashlib,sys,time
        from pathlib import Path
        from types import SimpleNamespace
        from controlcentre.store import Store
        from controlcentre.runtime import Runtime
        root=Path(sys.argv[1]);store=Store(root)
        store.put('providers',{'id':'relay','name':'Synthetic','kind':'worker','enabled':True,'config':{}})
        store.put('contacts',{'id':'contact','name':'Synthetic','number':'+12025550125','allowed':True})
        store.put('agents',{'id':'agent','name':'Synthetic','enabled':True,'prompt':'Be brief.','mode':'normal','max_duration_seconds':120})
        def dispatch(provider,task):
            time.sleep(.15)
            return 'call_'+hashlib.sha256(task['request_id'].encode()).hexdigest()[:32]
        backend=SimpleNamespace(client=lambda p:SimpleNamespace(close=lambda:None),dispatch=dispatch)
        rt=Runtime(root,root,store,SimpleNamespace(),backend=backend)
        reviews=[rt.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask hours.'}) for _ in range(2)]
        async def exercise():
            result=await asyncio.gather(*(rt.dial(r['approval_id']) for r in reviews),return_exceptions=True)
            assert sum(isinstance(r,dict) for r in result)==1
            assert sum(isinstance(r,ValueError) for r in result)==1
        asyncio.run(exercise())
        assert len(store.calls())==1
    ''')
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=4)
    assert result.returncode==0,result.stderr


def test_disabling_local_cache_during_call_still_displays_new_transcript(connected,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from controlcentre.app import create_app
    backend,store,_,_,provider=connected
    monkeypatch.setenv('PHONEDEX_VAULT_PASSPHRASE','synthetic-encryption-passphrase')
    provider['config']['cache_transcripts']=True
    store.put('providers',provider,provider['id'])
    backend.sync_once()
    assert len(store.get_call(CALL)['transcript'])==2
    app=create_app(tmp_path,tmp_path)
    provider['config']['cache_transcripts']=False
    store.put('providers',provider,provider['id'])
    original=backend.transport
    def later(request):
        response=original.handle_request(request)
        if request.url.path.endswith('/transcript'):
            data=json.loads(response.content)
            data['items'].append({'speaker':'caller','text':'A new sentence after caching was disabled.','ms':30})
            return httpx.Response(200,json=data)
        return response
    app.state.backend.transport=httpx.MockTransport(later)
    app.state.backend.sync_once()
    assert len(store.get_call(CALL)['transcript'])==2
    client=TestClient(app,base_url='http://127.0.0.1:8787',client=('127.0.0.1',1234))
    assert client.post('/api/auth/setup',json={'password':'synthetic-backend-password'}).status_code==200
    response=client.get(f'/api/calls/{CALL}')
    assert response.status_code==200
    assert len(response.json()['transcript'])==3


def test_remote_control_stays_pending_until_actual_acknowledgement(connected):
    backend,store,_,requests,_=connected
    backend.sync_once()
    original=backend.transport
    acknowledged=False
    command_id='cmd_'+'b'*32
    def commands(request):
        if request.method=='POST' and request.url.path.endswith('/steers'):
            requests.append(request)
            assert json.loads(request.content)=={'instruction':'Synthetic private instruction.'}
            return httpx.Response(202,json={'ok':True,'command_id':command_id,'status':'pending'})
        if request.url.path.endswith('/commands'):
            return httpx.Response(200,json={'commands':[{'command_id':command_id,'status':'acknowledged' if acknowledged else 'pending'}]})
        return original.handle_request(request)
    backend.transport=httpx.MockTransport(commands)
    result=backend.control(store.get_call(CALL),'steer',{'instruction':'Synthetic private instruction.'})
    assert result['state']=='pending'
    assert 'Synthetic private instruction.' not in json.dumps(store.pending_commands(CALL))
    backend.sync_once()
    assert store.pending_commands(CALL)
    acknowledged=True
    backend.sync_once()
    assert store.pending_commands(CALL)==[]
