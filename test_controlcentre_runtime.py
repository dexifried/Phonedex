"""Call approvals are exercised only with a fake child process."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from controlcentre.runtime import Runtime
from controlcentre.store import Store


@pytest.fixture
def runtime(tmp_path):
    core=tmp_path/'core'
    core.mkdir()
    (core/'phonedex.py').write_text('# synthetic core')
    (core/'.env').write_text('PHONEDEX_ALLOWED_RECIPIENTS=+12025550123\nPHONEDEX_MEDIA_PORT_FORWARDED=1\nPHONEDEX_TERMINATION_URI=sips:synthetic.invalid\nPHONEDEX_PUBLIC_IP=8.8.8.8\nPHONEDEX_CALLER_ID=+12025550124\nPHONEDEX_SIP_USERNAME=fixture-user\nPHONEDEX_SIP_PASSWORD=fixture-password\n')
    store=Store(tmp_path)
    store.put('contacts',{'id':'contact','name':'Synthetic contact','number':'+12025550123','allowed':True})
    store.put('agents',{'id':'agent','name':'Synthetic agent','enabled':True,'mode':'normal','prompt':'Be brief.','max_duration_seconds':120})
    return Runtime(tmp_path,core,store,SimpleNamespace()),store


def prepare(rt):
    return rt.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'Ask hours.'})


def test_preflight_never_launches_process(runtime):
    rt,store=runtime
    with patch('subprocess.Popen',side_effect=AssertionError('must not dial')):
        review=prepare(rt)
    assert review['approval_id']
    assert review['estimated_cost'] is None
    assert review['authorized_fields']==[]


def test_contact_and_core_allowlists_both_required(runtime):
    rt,store=runtime
    store.put('contacts',{'id':'contact','name':'Synthetic','number':'+12025550123','allowed':False})
    with pytest.raises(ValueError):prepare(rt)
    store.put('contacts',{'id':'contact','name':'Synthetic','number':'+12025550125','allowed':True})
    with pytest.raises(ValueError):prepare(rt)


def test_duration_and_prompt_validated(runtime):
    rt,_=runtime
    for duration in (-1,301,10000):
        with pytest.raises(ValueError):
            rt.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':duration,'prompt':'test'})
    with pytest.raises(ValueError):
        rt.preflight({'contact_id':'contact','agent_id':'agent','duration_seconds':60,'prompt':'x'*2001})


def test_changed_contact_invalidates_approval(runtime):
    rt,store=runtime
    review=prepare(rt)
    store.put('contacts',{'id':'contact','name':'Changed','number':'+12025550125','allowed':True})
    with pytest.raises(ValueError):asyncio.run(rt.dial(review['approval_id']))


def test_expired_approval_fails(runtime):
    rt,_=runtime
    review=prepare(rt)
    with patch('controlcentre.runtime.time.time',return_value=9999999999):
        with pytest.raises(ValueError):asyncio.run(rt.dial(review['approval_id']))


def test_approval_single_use_with_fake_process(runtime):
    rt,store=runtime
    review=prepare(rt)
    fake=SimpleNamespace(stdin=SimpleNamespace(write=lambda b:None,flush=lambda:None,close=lambda:None),
                         poll=lambda:None,pid=12345)
    with patch('controlcentre.runtime.subprocess.Popen',return_value=fake) as launch:
        call=asyncio.run(rt.dial(review['approval_id']))
        assert launch.call_count==1
        assert call['id'].startswith('call_')
        assert 'fixture-password' not in str(launch.call_args.args)
        with pytest.raises(ValueError):asyncio.run(rt.dial(review['approval_id']))


def test_control_requires_observed_fresh_call(runtime):
    rt,store=runtime
    store.record_call({'id':'call_test','state':'answered','controls':['hangup']})
    with pytest.raises(ValueError):asyncio.run(rt.control('call_test','hangup',{}))


def test_no_scheduled_or_conference_escape(runtime):
    rt,_=runtime
    with pytest.raises(ValueError):asyncio.run(rt.control('anything','conference',{}))


def test_two_dashboard_instances_share_single_call_reservation(runtime):
    rt,store=runtime
    other=Runtime(rt.data_dir,rt.core_root,store,SimpleNamespace())
    first,second=prepare(rt),prepare(other)
    fake=SimpleNamespace(stdin=SimpleNamespace(write=lambda b:None,close=lambda:None),poll=lambda:None,pid=12345)
    with patch('controlcentre.runtime.subprocess.Popen',return_value=fake) as launch:
        asyncio.run(rt.dial(first['approval_id']))
        with pytest.raises(ValueError,match='reserved'):
            asyncio.run(other.dial(second['approval_id']))
        assert launch.call_count==1
