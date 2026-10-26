import base64
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PASSPHRASE = 'offline unit test passphrase; not a credential'


def test_disappearing_sqlite_sidecar_zero_links_is_missing(tmp_path,monkeypatch):
    import stat
    from types import SimpleNamespace
    from controlcentre.crypto import _reject_links
    monkeypatch.setattr(Path,'lstat',lambda self:SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_nlink=0,st_file_attributes=0))
    with pytest.raises(FileNotFoundError):_reject_links(tmp_path/'control.sqlite3-wal',missing=True)


def cipher(tmp_path):
    from controlcentre.crypto import ProtectedCipher
    return ProtectedCipher(tmp_path / 'keys', PASSPHRASE)


def populated(tmp_path, value='457-892-136'):
    from controlcentre.vault import Vault
    vault = Vault(tmp_path, PASSPHRASE)
    profile = vault.create_profile('Personal')
    field = vault.put_field(profile['id'], 'health_card', 'Health card', value, 'ordinary')
    return vault, profile, field


def test_cipher_authenticates_context_and_tampering(tmp_path):
    from controlcentre.crypto import VaultIntegrityError
    protected = cipher(tmp_path)
    encrypted = protected.encrypt('secret test value', 'field:one')
    assert protected.decrypt(encrypted, 'field:one') == 'secret test value'
    assert encrypted != protected.encrypt('secret test value', 'field:one')
    with pytest.raises(VaultIntegrityError):
        protected.decrypt(encrypted, 'field:two')
    raw = bytearray(base64.urlsafe_b64decode(encrypted.split(':', 1)[1]))
    raw[-1] ^= 1
    altered = 'v1:' + base64.urlsafe_b64encode(raw).decode()
    with pytest.raises(VaultIntegrityError):
        protected.decrypt(altered, 'field:one')


def test_passphrase_wrapping_persists_and_wrong_password_fails_closed(tmp_path):
    from controlcentre.crypto import ProtectedCipher, VaultLockedError
    blob = cipher(tmp_path).encrypt('fixture private text', 'record')
    assert cipher(tmp_path).decrypt(blob, 'record') == 'fixture private text'
    with pytest.raises(VaultLockedError):
        ProtectedCipher(tmp_path / 'keys', 'incorrect passphrase').decrypt(blob, 'record')
    assert b'fixture private text' not in b''.join(p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


def test_linux_without_secure_storage_is_lazy_and_locked(tmp_path, monkeypatch):
    import controlcentre.crypto as crypto
    monkeypatch.setattr(crypto.sys, 'platform', 'linux')
    monkeypatch.setattr(crypto, '_keyring', lambda: None)
    from controlcentre.vault import Vault
    vault = Vault(tmp_path)
    assert vault.profiles() == []
    with pytest.raises(crypto.VaultLockedError):
        vault.cipher.encrypt('never write plaintext', 'test')
    assert not list((tmp_path / 'vault-keys').glob('*.json'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires real Windows user DPAPI')
def test_windows_dpapi_real_roundtrip_and_protected_key(tmp_path):
    from controlcentre.crypto import ProtectedCipher
    protected = ProtectedCipher(tmp_path)
    blob = protected.encrypt('DPAPI synthetic data', 'test')
    assert ProtectedCipher(tmp_path).decrypt(blob, 'test') == 'DPAPI synthetic data'
    record = json.loads((tmp_path / 'protected-key.json').read_text())
    assert record['mode'] == 'dpapi'
    assert len(base64.b64decode(record['wrapped'])) > 32


def test_field_defaults_mask_all_values_and_force_sensitivity(tmp_path):
    vault, profile, field = populated(tmp_path)
    assert field['sensitivity'] == 'sensitive'
    assert field['value_masked'] == '••••••••'
    assert 'value' not in field
    ordinary = vault.put_field(profile['id'], 'city', 'City', 'Toronto', 'ordinary')
    assert ordinary['value_masked'] == '••••••••'
    assert ordinary['sensitivity'] == 'ordinary'
    assert '457-892-136' not in json.dumps(vault.fields(profile['id']))
    assert vault.reveal(field['id']) == '457-892-136'


def test_ciphertext_cannot_be_swapped_between_fields(tmp_path):
    from controlcentre.crypto import VaultIntegrityError
    vault, profile, field = populated(tmp_path)
    other = vault.put_field(profile['id'], 'ssn', 'SSN', '785430921', 'sensitive')
    with sqlite3.connect(tmp_path / 'vault.sqlite3') as db:
        ciphertext = db.execute('SELECT ciphertext FROM fields WHERE id=?', (field['id'],)).fetchone()[0]
        db.execute('UPDATE fields SET ciphertext=? WHERE id=?', (ciphertext, other['id']))
    with pytest.raises(VaultIntegrityError):
        vault.reveal(other['id'])
    assert vault.redact('must never persist this') == '[redacted: vault unavailable]'


def test_explicit_grant_is_required_even_for_ordinary_field(tmp_path):
    vault, profile, _ = populated(tmp_path)
    field = vault.put_field(profile['id'], 'city', 'City', 'Toronto', 'ordinary')
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call-a', 'Clinic', 'booking')


def test_grant_requires_exact_field_call_recipient_and_purpose(tmp_path):
    vault, profile, field = populated(tmp_path)
    other = vault.put_field(profile['id'], 'insurance', 'Insurance', 'TEST-POLICY-ABC', 'sensitive')
    grant = vault.grant(field['id'], 'call-a', 'Clinic', 'booking', 120)
    assert vault.request_profile_field(field['id'], 'call-a', 'Clinic', 'booking') == '457-892-136'
    for args in [(other['id'], 'call-a', 'Clinic', 'booking'), (field['id'], 'call-b', 'Clinic', 'booking'),
                 (field['id'], 'call-a', 'Other clinic', 'booking'), (field['id'], 'call-a', 'Clinic', 'billing')]:
        with pytest.raises(PermissionError):
            vault.request_profile_field(*args)
    assert grant['field_id'] == field['id']
    assert any(item['decision'] == 'deny' for item in vault.audit())
    vault.revoke(grant['id'])
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call-a', 'Clinic', 'booking')


def test_expiry_is_bounded_and_enforced(tmp_path, monkeypatch):
    import controlcentre.vault as implementation
    vault, _, field = populated(tmp_path)
    for seconds in (0, -1, 301, True):
        with pytest.raises(ValueError):
            vault.grant(field['id'], 'call', 'Clinic', 'booking', seconds)
    now = implementation.time.time()
    vault.grant(field['id'], 'call', 'Clinic', 'booking', 2)
    monkeypatch.setattr(implementation.time, 'time', lambda: now + 3)
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call', 'Clinic', 'booking')


def test_update_revokes_grants_for_previous_value(tmp_path):
    vault, profile, field = populated(tmp_path)
    vault.grant(field['id'], 'call', 'Clinic', 'booking', 60)
    updated = vault.put_field(profile['id'], 'health_card', 'Health card', '888999000', 'sensitive')
    assert updated['id'] == field['id']
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call', 'Clinic', 'booking')


def test_deleting_fields_profiles_and_all_removes_access(tmp_path):
    from controlcentre.crypto import VaultLockedError
    vault, profile, field = populated(tmp_path)
    vault.grant(field['id'], 'call', 'Clinic', 'booking', 60)
    vault.delete_field(field['id'])
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call', 'Clinic', 'booking')
    assert vault.fields(profile['id']) == []
    fresh = vault.put_field(profile['id'], 'passport', 'Passport', 'TEST98765', 'sensitive')
    old_ciphertext = vault.cipher.encrypt('old', 'old')
    vault.delete_profile(profile['id'])
    assert vault.profiles() == []
    with pytest.raises(KeyError):
        vault.reveal(fresh['id'])
    vault.delete_all()
    assert not (tmp_path / 'vault-keys' / 'protected-key.json').exists()
    with pytest.raises(VaultLockedError):
        vault.cipher.decrypt(old_ciphertext, 'old')
    assert not (tmp_path / 'vault-keys' / 'protected-key.json').exists()
    assert any(item['event'] == 'delete_all' for item in vault.audit())


def test_redacts_exact_and_normalized_spoken_digit_values(tmp_path):
    vault, profile, _ = populated(tmp_path)
    vault.put_field(profile['id'], 'name', 'Legal name', 'Avery Example', 'sensitive')
    vault.put_field(profile['id'], 'regex', 'Literal', 'x.y+[z]', 'sensitive')
    text = 'Avery Example: 457892136 / 457 892 136 / 4-5-7-8-9-2-1-3-6; x.y+[z]; xyzz'
    assert vault.redact(text) == '[REDACTED]: [REDACTED] / [REDACTED] / [REDACTED]; [REDACTED]; xyzz'


def test_redaction_of_tiny_values_does_not_corrupt_words(tmp_path):
    vault, profile, _ = populated(tmp_path)
    vault.put_field(profile['id'], 'initial', 'Initial', 'a', 'ordinary')
    vault.put_field(profile['id'], 'unit', 'Unit', '12', 'ordinary')
    assert vault.redact('a cat at 12, 123 and stable') == '[REDACTED] cat at [REDACTED], 123 and stable'


def test_audit_and_database_never_contain_field_values(tmp_path):
    value = '899777123'
    vault, _, field = populated(tmp_path, value)
    vault.grant(field['id'], 'call', value, 'verify ' + value, 60)
    vault.request_profile_field(field['id'], 'call', value, 'verify ' + value)
    vault.reveal(field['id'])
    assert value not in json.dumps(vault.audit())
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert value.encode() not in path.read_bytes(), path


def test_revocation_is_seen_by_other_vault_instances(tmp_path):
    from controlcentre.vault import Vault
    vault, _, field = populated(tmp_path)
    other = Vault(tmp_path, PASSPHRASE)
    grant = vault.grant(field['id'], 'call', 'Clinic', 'booking', 60)
    assert other.request_profile_field(field['id'], 'call', 'Clinic', 'booking') == '457-892-136'
    vault.revoke(grant['id'])
    with pytest.raises(PermissionError):
        other.request_profile_field(field['id'], 'call', 'Clinic', 'booking')


def test_linux_os_keyring_keeps_data_key_out_of_files(tmp_path, monkeypatch):
    import controlcentre.crypto as crypto

    class SecureTestKeyring:
        def __init__(self):
            self.records = {}
        def set_password(self, service, account, value):
            self.records[(service, account)] = value
        def get_password(self, service, account):
            return self.records.get((service, account))
        def delete_password(self, service, account):
            del self.records[(service, account)]

    backend = SecureTestKeyring()
    monkeypatch.setattr(crypto.sys, 'platform', 'linux')
    monkeypatch.setattr(crypto, '_keyring', lambda: backend)
    protected = crypto.ProtectedCipher(tmp_path)
    blob = protected.encrypt('synthetic protected value', 'test')
    assert protected.decrypt(blob, 'test') == 'synthetic protected value'
    encoded_key = next(iter(backend.records.values()))
    assert encoded_key not in (tmp_path / 'protected-key.json').read_text()
    protected.destroy()
    assert backend.records == {}
    with pytest.raises(crypto.VaultLockedError):
        protected.decrypt(blob, 'test')


def test_corrupt_wrapped_key_is_not_replaced(tmp_path):
    from controlcentre.crypto import VaultLockedError
    protected = cipher(tmp_path)
    protected.encrypt('synthetic secret', 'test')
    key_path = tmp_path / 'keys' / 'protected-key.json'
    damaged = key_path.read_bytes().replace(b'"version": 1', b'"version": 9')
    key_path.write_bytes(damaged)
    with pytest.raises(VaultLockedError):
        protected.encrypt('other synthetic secret', 'test')
    assert key_path.read_bytes() == damaged


def test_complete_deletion_does_not_erase_provider_keys(tmp_path):
    from controlcentre.crypto import ProtectedCipher
    vault, _, _ = populated(tmp_path)
    provider = ProtectedCipher(tmp_path / 'provider-keys', PASSPHRASE)
    blob = provider.encrypt('provider unit test credential', 'provider:test')
    vault.delete_all()
    assert provider.decrypt(blob, 'provider:test') == 'provider unit test credential'


def test_disclosure_and_revoke_have_transactional_ordering(tmp_path, monkeypatch):
    import threading
    from controlcentre.vault import Vault
    vault, _, field = populated(tmp_path)
    revoker = Vault(tmp_path, PASSPHRASE)
    grant = vault.grant(field['id'], 'call', 'Clinic', 'booking', 60)
    inside_read, release_read, revoke_started = threading.Event(), threading.Event(), threading.Event()
    original_decrypt = vault.cipher.decrypt

    def blocking_decrypt(blob, purpose):
        if purpose.startswith('field:'):
            inside_read.set()
            assert release_read.wait(5)
        return original_decrypt(blob, purpose)

    def revoke():
        revoke_started.set()
        return revoker.revoke(grant['id'])

    monkeypatch.setattr(vault.cipher, 'decrypt', blocking_decrypt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        disclosure = pool.submit(vault.request_profile_field, field['id'], 'call', 'Clinic', 'booking')
        assert inside_read.wait(5)
        revocation = pool.submit(revoke)
        assert revoke_started.wait(5)
        assert not revocation.done()
        release_read.set()
        assert disclosure.result(timeout=5) == '457-892-136'
        assert revocation.result(timeout=5)['revoked'] is True
    with pytest.raises(PermissionError):
        vault.request_profile_field(field['id'], 'call', 'Clinic', 'booking')


@pytest.mark.parametrize('key,label', [
    ('SIN', 'Number'), ('ssn', 'Number'), ('passport', 'Travel'), ('driver_license', 'Number'),
    ('national_id', 'Identity'), ('ohip', 'Card'), ('dob', 'Date'), ('bank_account', 'Number'),
    ('insurance_policy', 'Policy'), ('credit_card', 'Number'), ('routing_number', 'Number'),
])
def test_sensitive_categories_cannot_be_downgraded(tmp_path, key, label):
    from controlcentre.vault import Vault
    vault = Vault(tmp_path, PASSPHRASE)
    profile = vault.create_profile('Test')
    assert vault.put_field(profile['id'], key, label, 'synthetic value', 'ordinary')['sensitivity'] == 'sensitive'


def test_delete_all_invalidates_previously_used_cipher_instances(tmp_path):
    from controlcentre.crypto import ProtectedCipher, VaultLockedError, VaultIntegrityError
    vault, _, _ = populated(tmp_path)
    another = ProtectedCipher(tmp_path / 'vault-keys', PASSPHRASE)
    blob = another.encrypt('synthetic old value', 'test')
    assert another.decrypt(blob, 'test') == 'synthetic old value'
    vault.delete_all()
    with pytest.raises(VaultLockedError):
        another.decrypt(blob, 'test')
    profile = vault.create_profile('New')
    vault.put_field(profile['id'], 'name', 'Name', 'Synthetic new value', 'ordinary')
    with pytest.raises(VaultIntegrityError):
        another.decrypt(blob, 'test')




def test_vault_categories_are_informational_and_never_allow_implicit_access(tmp_path):
    from controlcentre.vault import Vault
    vault = Vault(tmp_path, PASSPHRASE)
    profile = vault.create_profile('Synthetic')
    for category in ('personal', 'contact', 'address', 'government', 'health', 'insurance', 'financial', 'other'):
        field = vault.put_field(profile['id'], 'custom_' + category, 'Custom', 'synthetic value', category)
        assert field['sensitivity'] == 'sensitive'
        assert field['category'] == category
        with pytest.raises(PermissionError):
            vault.request_profile_field(field['id'], 'call', 'Clinic', 'booking')


def test_vault_rejects_symlinked_data_directory(tmp_path):
    from controlcentre.vault import Vault
    from controlcentre.crypto import VaultLockedError
    target = tmp_path / 'actual'
    target.mkdir()
    link = tmp_path / 'linked'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation needs OS privileges')
    with pytest.raises(VaultLockedError):
        Vault(link, PASSPHRASE)
    assert list(target.iterdir()) == []


def test_cipher_rejects_hardlinked_protected_key(tmp_path):
    from controlcentre.crypto import VaultLockedError
    protected = cipher(tmp_path)
    protected.encrypt('synthetic value', 'test')
    clone = tmp_path / 'linked-key'
    clone.hardlink_to(tmp_path / 'keys' / 'protected-key.json')
    with pytest.raises(VaultLockedError):
        protected.decrypt('v1:invalid', 'test')


def test_vault_database_is_private_to_current_user(tmp_path):
    from controlcentre.vault import Vault
    vault = Vault(tmp_path / 'private', PASSPHRASE)
    vault.create_profile('Synthetic')
    if sys.platform == 'win32':
        import subprocess
        result = subprocess.run(['icacls', str(vault.path)], capture_output=True, text=True, check=True)
        assert 'Everyone:' not in result.stdout
        assert 'BUILTIN\\Users:' not in result.stdout
    else:
        assert vault.path.stat().st_mode & 0o077 == 0
        assert vault.data_dir.stat().st_mode & 0o077 == 0
