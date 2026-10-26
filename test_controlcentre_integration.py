"""Offline integration contracts: no carrier, OAuth, or model requests."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from controlcentre.integration import DashboardControl, instrument, redact_text
from controlcentre.store import Store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('PHONEDEX_DASHBOARD_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PHONEDEX_DASHBOARD_EVENTS', '1')
    monkeypatch.setenv('PHONEDEX_TRANSCRIPT_MODE', 'local')
    return tmp_path, Store(tmp_path)


def options():
    return SimpleNamespace(to='+12025550123', mode='normal', duration=60)


def test_core_hook_is_inert_by_default(monkeypatch):
    monkeypatch.delenv('PHONEDEX_DASHBOARD_EVENTS', raising=False)
    original = object()
    assert instrument(options(), original) is original
    assert instrument(options(), None) is None


def test_lifecycle_transcript_dedup_and_no_reasoning(workspace):
    folder, store = workspace
    async def run():
        control = DashboardControl(options(), data_dir=folder)
        await control.status('answered')
        event = {'type':'session.input_transcript.delta','event_id':'evt_test',
                 'delta':'Synthetic completed utterance','end_ms':500}
        control.on_live_event(event)
        control.on_live_event(event)
        control.on_live_event({'type':'reasoning.delta','delta':'DO_NOT_PERSIST'})
        await control.close('ended')
        call = store.get_call(control.id)
        assert call['state'] == 'ended'
        assert call['task_success'] is None
        assert call['cost'] is None
        assert len(call['transcript']) == 1
        assert call['number_masked'] != options().to
        assert 'DO_NOT_PERSIST' not in str(call)
        assert call['controls'] == []
    asyncio.run(run())


def test_transcript_off_honored(workspace, monkeypatch):
    folder, store = workspace
    monkeypatch.setenv('PHONEDEX_TRANSCRIPT_MODE', 'off')
    async def run():
        control = DashboardControl(options(), data_dir=folder)
        control.on_live_event({'type':'session.output_transcript.delta',
                               'delta':'Must not persist','event_id':'x'})
        await control.close('ended')
        assert store.get_call(control.id)['transcript'] == []
    asyncio.run(run())


def test_hangup_uses_existing_control_without_killing_process(workspace):
    folder, store = workspace
    async def run():
        original = SimpleNamespace(id='call_'+'a'*32, hangup=asyncio.Event(),
                                   model=None,status=AsyncMock(),on_live_event=lambda e:None)
        control = DashboardControl(options(), original, data_dir=folder)
        store.queue_command(control.id, 'hangup', {})
        await control.consume_commands()
        assert original.hangup.is_set()
        assert store.pending_commands(control.id) == []
        await control.close('ended')
    asyncio.run(run())


def test_private_instruction_uses_existing_sideband(workspace):
    folder, store = workspace
    async def run():
        control = DashboardControl(options(), data_dir=folder)
        backend=SimpleNamespace(sideband=object(),send_private_steer=AsyncMock())
        control.model=SimpleNamespace(backend=backend)
        store.queue_command(control.id, 'steer', {'instruction':'Please be concise.'})
        await control.consume_commands()
        backend.send_private_steer.assert_awaited_once()
        assert 'Please be concise.' not in str(store.events())
        await control.close('ended')
    asyncio.run(run())


def test_unsupported_command_rejected(workspace):
    folder, store=workspace
    async def run():
        control=DashboardControl(options(),data_dir=folder)
        store.queue_command(control.id,'transfer',{})
        await control.consume_commands()
        assert not control.hangup.is_set()
        assert store.pending_commands(control.id)==[]
        await control.close('ended')
    asyncio.run(run())


def test_vault_redaction_before_any_transcript_persistence(workspace):
    folder,store=workspace
    from controlcentre.vault import Vault
    vault=Vault(folder)
    profile=vault.create_profile('Synthetic')
    field=vault.put_field(profile['id'],'health_card','Health card','SYNTHETIC-9876543210','sensitive')
    assert '9876543210' not in redact_text('Card SYNTHETIC-9876543210',folder)


def test_no_source_text_in_safe_log(workspace):
    folder,store=workspace
    async def run():
        control=DashboardControl(options(),data_dir=folder)
        control.on_live_event({'type':'error','message':'Bearer secret-value-should-not-appear'})
        await control.close('failed')
        assert 'secret-value' not in str(store.events())
    asyncio.run(run())


def test_dashboard_storage_failure_never_breaks_existing_call(workspace):
    folder,_=workspace
    async def run():
        original=SimpleNamespace(id='call_'+'b'*32,hangup=asyncio.Event(),model=None,
                                 status=AsyncMock(),on_live_event=lambda e:None)
        control=DashboardControl(options(),original,data_dir=folder)
        with patch.object(getattr(control.store,'_inner',control.store),'add_event',side_effect=OSError('synthetic disk failure')):
            await control.status('answered')
        original.status.assert_awaited_once_with('answered',None)
        await control.close('ended')
        original.status.assert_awaited_once()
    asyncio.run(run())


def test_fragmented_vault_secret_is_redacted_before_disk(workspace):
    folder,store=workspace
    from controlcentre.vault import Vault
    vault=Vault(folder)
    p=vault.create_profile('Synthetic')
    vault.put_field(p['id'],'insurance_id','Member ID','SYNTHETIC-9876543210','sensitive')
    async def run():
        control=DashboardControl(options(),data_dir=folder)
        control.on_live_event({'type':'session.input_transcript.delta','event_id':'split1','delta':'SYNTHETIC-987'})
        control.on_live_event({'type':'session.input_transcript.delta','event_id':'split2','delta':'6543210'})
        assert store.get_call(control.id)['transcript']==[]
        control.on_live_event({'type':'session.input_transcript.done','event_id':'final'})
        text=''.join(x['text'] for x in store.get_call(control.id)['transcript'])
        assert '987' not in text and '6543210' not in text
        await control.close('ended')
    asyncio.run(run())


def test_private_instruction_requires_real_model_ack(workspace):
    folder,store=workspace
    async def run():
        control=DashboardControl(options(),data_dir=folder)
        control.model=SimpleNamespace(backend=SimpleNamespace(sideband=object(),send_private_steer=AsyncMock()))
        command=store.queue_command(control.id,'steer',{'instruction':'Be concise.'})
        await control.consume_commands()
        with store.connect() as db:
            assert db.execute('SELECT state FROM commands WHERE id=?',(command['id'],)).fetchone()[0]=='sent'
        control.on_live_event({'type':'error','client_event_id':'cmd_'+command['id']})
        with store.connect() as db:
            assert db.execute('SELECT state FROM commands WHERE id=?',(command['id'],)).fetchone()[0]=='failed'
        await control.close('ended')
    asyncio.run(run())
