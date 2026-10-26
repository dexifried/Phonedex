"""Fake model sideband verifies exact authorized field injection, no phone calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from controlcentre.integration import DashboardControl
from controlcentre.store import Store
from controlcentre.vault import Vault
from harness.delegation import DelegationBroker


def test_profile_field_uses_trusted_recipient_and_actual_model_ack(tmp_path,monkeypatch):
    monkeypatch.setenv('PHONEDEX_DASHBOARD_EVENTS','1')
    monkeypatch.setenv('PHONEDEX_TRANSCRIPT_MODE','local')
    vault=Vault(tmp_path)
    p=vault.create_profile('Synthetic')
    field=vault.put_field(p['id'],'insurance','Insurance member ID','SYNTHETIC-ONLY-83920')
    store=Store(tmp_path)
    async def run():
        control=DashboardControl(SimpleNamespace(to='+12025550123',mode='normal'),data_dir=tmp_path)
        sideband=SimpleNamespace(sideband=object(),send_private_steer=AsyncMock())
        control.model=SimpleNamespace(backend=sideband)
        purpose='Verify synthetic insurance membership'
        with pytest.raises(PermissionError):
            DelegationBroker(control).structured_tool_request('request_profile_field',{'field_id':field['id'],'purpose':purpose})
        grant=vault.grant(field['id'],control.id,'+12025550123',purpose,120)
        command=store.queue_command(control.id,'profile_field',{'field_id':field['id'],'purpose':purpose})
        await control.consume_commands()
        sideband.send_private_steer.assert_awaited_once()
        assert 'SYNTHETIC-ONLY-83920' in sideband.send_private_steer.call_args.args[1]
        assert 'SYNTHETIC-ONLY-83920' not in str(store.events())
        control.on_live_event({'type':'session.instructions.appended','client_event_id':'cmd_'+command['id']})
        vault.revoke(grant['id'])
        with pytest.raises(PermissionError):control.request_profile_field(field['id'],purpose)
        with pytest.raises(PermissionError):control.request_profile_field(field['id'],'different purpose')
        await control.close()
    asyncio.run(run())
