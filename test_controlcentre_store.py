from controlcentre.store import Store


def test_partial_call_updates_preserve_terminal_state_and_unknown_outcomes(tmp_path):
    store = Store(tmp_path)
    store.record_call({'id': 'c1', 'state': 'active', 'contact': 'Office'})
    store.transcript('c1', 'user', 'Hello', 20, 'event1')
    assert not store.transcript('c1', 'user', 'Hello', 20, 'event1')
    store.record_call({'id': 'c1', 'state': 'completed', 'duration_seconds': 8})
    store.record_call({'id': 'c1', 'state': 'active', 'provider': 'SIP'})
    call = store.get_call('c1')
    assert call['state'] == 'completed'
    assert call['contact'] == 'Office'
    assert len(call['transcript']) == 1
    assert call['task_success'] is None and call['cost'] is None
    assert Store(tmp_path).get_call('c1') == call


def test_commands_ack_and_changes_are_persistent(tmp_path):
    store = Store(tmp_path)
    store.record_call({'id': 'c1'})
    command = store.queue_command('c1', 'hangup', {})
    assert store.pending_commands('c1')[0]['id'] == command['id']
    store.ack_command(command['id'], 'completed')
    assert store.pending_commands('c1') == []
    assert store.changes_since(0)[-1]['revision'] == store.revision()


def test_private_reasoning_and_raw_number_are_not_persisted(tmp_path):
    store = Store(tmp_path)
    store.record_call({'id': 'c1', 'number': '+12025550123', 'reasoning': 'private'})
    store.add_event({'kind': 'call', 'message': 'ready', 'token': 'secret', 'reasoning': 'private'})
    assert 'reasoning' not in store.get_call('c1')
    assert '+12025550123' not in str(store.get_call('c1'))
    assert 'secret' not in str(store.events())


def test_call_progress_never_regresses_on_reordered_instrumentation(tmp_path):
    store = Store(tmp_path)
    store.record_call({'id': 'c1', 'state': 'active'})
    store.record_call({'id': 'c1', 'state': 'ringing'})
    assert store.get_call('c1')['state'] == 'active'


def test_independent_writers_merge_call_updates_atomically(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(3)
    class ScheduledReadStore(Store):
        # Force writers that use a nontransactional read/replace sequence to
        # observe the same previous snapshot. Each read still hits real SQLite.
        waited = False
        def get(self, kind, object_id):
            result = super().get(kind, object_id)
            if kind == 'calls' and not self.waited:
                self.waited = True
                barrier.wait(timeout=5)
            return result
    Store(tmp_path).record_call({'id': 'c1'})
    writers = [ScheduledReadStore(tmp_path) for _ in range(3)]
    patches = [{'contact': 'Office'}, {'provider': 'Carrier'}, {'agent_name': 'Assistant'}]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda pair: pair[0].record_call({'id': 'c1', **pair[1]}), zip(writers, patches)))
    call = Store(tmp_path).get_call('c1')
    assert all(call[key] == value for patch in patches for key, value in patch.items())


def test_metadata_database_rejects_hardlinked_state(tmp_path):
    import os
    import pytest
    target = Store(tmp_path / 'target')
    other = tmp_path / 'other'
    other.mkdir()
    os.link(target.path, other / 'control.sqlite3')
    with pytest.raises(ValueError):
        Store(other)


def test_analytics_separates_estimates_charges_and_missing_timestamps():
    from controlcentre.analytics import analytics
    from datetime import datetime, timezone
    date = datetime.now(timezone.utc).isoformat()
    calls = [
        {'id': 'a', 'state': 'ended', 'started_at': date, 'task_success': True, 'cost': 2.5, 'cost_kind': 'confirmed', 'duration_seconds': 42, 'agent_id': 'agent1', 'agent_name': 'Booking'},
        {'id': 'b', 'state': 'failed', 'started_at': date, 'task_success': False, 'cost': 1.5, 'cost_kind': 'actual', 'duration_seconds': 12, 'error_code': 'carrier_rejected', 'agent_id': 'agent1', 'agent_name': 'Booking'},
        {'id': 'c', 'state': 'ended', 'task_success': None, 'cost': 10, 'cost_kind': 'estimated', 'duration_seconds': None},
        {'id': 'd', 'state': 'ended', 'started_at': 'invalid', 'task_success': None, 'cost': None},
    ]
    result = analytics(calls, days=7)
    assert result['total_cost'] == result['confirmed_cost'] == 4
    assert result['estimated_cost'] == 10
    assert result['average_confirmed_cost'] == 2
    assert result['cost_per_completed_task'] is None
    assert result['success_rate'] == 50
    assert result['unknown_date_calls'] == 2
    assert sum(day['calls'] for day in result['daily']) == 2
    assert result['errors'] == [{'code': 'carrier_rejected', 'count': 1}]
    assert result['by_agent'][0]['total_calls'] == 2
    assert sum(hour['calls'] for hour in result['by_hour']) == 2
    assert sum(bucket['count'] for bucket in result['duration_distribution']) == 2


def test_cost_per_completed_task_only_uses_complete_confirmed_records():
    from controlcentre.analytics import analytics
    calls = [{'state': 'ended', 'task_success': True, 'cost': 3, 'cost_kind': 'confirmed'},
             {'state': 'failed', 'task_success': False, 'cost': 1, 'cost_kind': 'confirmed'}]
    assert analytics(calls)['cost_per_completed_task'] == 4
    calls[1]['cost_kind'] = 'estimated'
    assert analytics(calls)['cost_per_completed_task'] is None


def test_disappearing_optional_sidecar_does_not_break_reads(tmp_path, monkeypatch):
    import controlcentre.store as module
    store = Store(tmp_path)
    original = module._reject_links
    def disappearing(path, **kwargs):
        if str(path).endswith('-wal'):
            raise FileNotFoundError('sidecar disappeared between stat and ACL check')
        return original(path, **kwargs)
    monkeypatch.setattr(module, '_reject_links', disappearing)
    assert store.calls() == []


def test_optional_sidecar_acl_failure_remains_fatal(tmp_path, monkeypatch):
    import controlcentre.store as module
    import pytest
    from controlcentre.crypto import VaultLockedError
    store = Store(tmp_path)
    original = module._reject_links
    def rejected(path, **kwargs):
        if str(path).endswith('-wal'):
            raise VaultLockedError('Permission check failed')
        return original(path, **kwargs)
    monkeypatch.setattr(module, '_reject_links', rejected)
    with pytest.raises(VaultLockedError):
        store.calls()


def test_windows_acl_missing_file_remains_distinct_from_permission_failure(tmp_path, monkeypatch):
    import os
    import ctypes
    import pytest
    import controlcentre.crypto as crypto
    if os.name != 'nt':
        pytest.skip('Windows ACL syscall boundary')
    path = tmp_path / 'disappears.txt'
    path.write_text('synthetic')
    original_loader = ctypes.WinDLL
    class MissingSetter:
        def __call__(self, *args):
            path.unlink()
            ctypes.set_last_error(2)
            return False
    class AdvapiProxy:
        def __init__(self, native):
            self.native = native
            self.SetFileSecurityW = MissingSetter()
        def __getattr__(self, key):
            return getattr(self.native, key)
    def loader(name, *args, **kwargs):
        native = original_loader(name, *args, **kwargs)
        return AdvapiProxy(native) if name == 'advapi32' else native
    monkeypatch.setattr(ctypes, 'WinDLL', loader)
    with pytest.raises(FileNotFoundError):
        crypto._windows_private_acl(path)
