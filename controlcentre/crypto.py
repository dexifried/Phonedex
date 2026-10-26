"""Authenticated encryption with user-protected, never plaintext, data keys."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class VaultLockedError(ValueError):
    """A secure key store or the correct passphrase is required."""


class VaultIntegrityError(ValueError):
    """Encrypted data failed authentication."""


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SERVICE = 'PhoneDex Control Centre'
_KEY_AAD = b'phonedex:protected-key:v1'


def _keyring():
    """Only accept OS-backed keyrings, never plaintext third-party fallbacks."""
    try:
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        approved = ('keyring.backends.SecretService', 'keyring.backends.kwallet',
                    'keyring.backends.macOS', 'keyring.backends.Windows')
        if module.startswith(approved) and backend.priority > 0:
            return backend
    except Exception:
        pass
    return None


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    """Use current-user Windows DPAPI; explicit pointer types support 64-bit."""
    if sys.platform != 'win32':
        raise VaultLockedError('This key requires its Windows user account')

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    blob_pointer = ctypes.POINTER(DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [blob_pointer, ctypes.c_wchar_p, blob_pointer,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, blob_pointer]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [blob_pointer, ctypes.POINTER(ctypes.c_wchar_p), blob_pointer,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, blob_pointer]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DATA_BLOB(len(data), buffer)
    result = DATA_BLOB()
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(source), 'PhoneDex encryption key', None,
                                      None, None, 1, ctypes.byref(result))
    if not ok:
        raise VaultLockedError('The protected encryption key is unavailable')
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _derive(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode('utf-8'))



def _reject_links(path: Path, *, directory=False, missing=False, check_owner=False):
    try:
        status = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise VaultLockedError('The private storage path is unavailable') from None
    import stat
    if stat.S_ISLNK(status.st_mode) or getattr(status, 'st_file_attributes', 0) & 0x400:
        raise VaultLockedError('Linked storage paths are not permitted')
    if directory:
        if not stat.S_ISDIR(status.st_mode):
            raise VaultLockedError('A private storage directory is required')
    elif stat.S_ISREG(status.st_mode) and status.st_nlink==0:
        # Windows can stat a SQLite sidecar after its last handle unlinked it.
        # Treat that precise race as disappearance, never as an ACL failure.
        raise FileNotFoundError('Private storage file no longer exists')
    elif not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise VaultLockedError('A private unlinked storage file is required')
    if check_owner and os.name != 'nt' and status.st_uid != os.getuid():
        raise VaultLockedError('Private storage must belong to the current user')


def _windows_private_acl(path: Path):
    """Protected DACL: current process user and Local System, with inheritance."""
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    token, sid_text, descriptor = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError()
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise OSError()
        # TOKEN_USER begins with SID_AND_ATTRIBUTES, whose first field is PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError()
        sddl = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;' + sid_text.value + ')'
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError()
        if not advapi.SetFileSecurityW(str(path), 0x00000004 | 0x80000000, descriptor):
            if ctypes.get_last_error() in (2, 3):
                raise FileNotFoundError('Private storage file no longer exists')
            raise OSError()
    except FileNotFoundError:
        raise
    except Exception:
        raise VaultLockedError('Private storage permissions could not be secured') from None
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


def private_directory(path: Path) -> Path:
    """Secure only the named app directory; reject links in its full ancestry."""
    path = Path(os.path.abspath(path))
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            _reject_links(item, directory=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_links(path, directory=True, check_owner=True)
    if os.name == 'nt':
        _windows_private_acl(path)
    else:
        os.chmod(path, 0o700)
    return path


def private_file(path: Path, *, create=False):
    """Reject symlinks/reparse points and hardlinks before opening local state."""
    _reject_links(path, missing=True, check_owner=True)
    if create and not path.exists():
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(str(path), flags, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
    _reject_links(path, missing=not create, check_owner=True)
    if path.exists():
        try:
            if os.name == 'nt':
                _windows_private_acl(path)
            else:
                os.chmod(path, 0o600)
        except FileNotFoundError:
            if create:
                raise
            # Optional SQLite sidecars may disappear between stat and chmod/ACL.
            # Other OS and ACL failures must propagate.


class ProtectedCipher:
    """One random AES-256 key protected by DPAPI, OS keyring, or scrypt+AES-GCM.

    Construction is lazy. Decryption never creates a missing key. No decrypted
    key is cached, so another instance observes erasure on its next operation.
    """

    def __init__(self, data_dir: Path, passphrase: str | None = None):
        self.data_dir = Path(os.path.abspath(data_dir))
        self.key_path = self.data_dir / 'protected-key.json'
        self._passphrase = passphrase
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.data_dir.resolve()), threading.RLock())

    @contextmanager
    def _exclusive(self):
        with self._lock:
            private_directory(self.data_dir)
            private_file(self.data_dir / '.key.lock', create=True)
            private_file(self.key_path)
            with (self.data_dir / '.key.lock').open('a+b') as handle:
                if handle.tell() == 0:
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_key(self, *, create: bool) -> bytes:
        if not self.key_path.exists():
            if not create:
                raise VaultLockedError('The encryption key is unavailable')
            return self._create_key()
        try:
            record = json.loads(self.key_path.read_text(encoding='utf-8'))
            if record.get('version') != 1:
                raise ValueError()
            if record['mode'] == 'dpapi':
                key = _dpapi(base64.b64decode(record['wrapped'], validate=True), decrypt=True)
            elif record['mode'] == 'passphrase':
                if not self._passphrase:
                    raise VaultLockedError('Unlock the vault with its passphrase')
                salt = base64.b64decode(record['salt'], validate=True)
                nonce = base64.b64decode(record['nonce'], validate=True)
                if len(salt) != 16 or len(nonce) != 12:
                    raise ValueError()
                key = AESGCM(_derive(self._passphrase, salt)).decrypt(
                    nonce, base64.b64decode(record['wrapped'], validate=True), _KEY_AAD)
            elif record['mode'] == 'keyring':
                backend = _keyring()
                if backend is None:
                    raise VaultLockedError('A secure OS keyring is required')
                encoded = backend.get_password(_SERVICE, record['account'])
                key = base64.b64decode(encoded, validate=True) if encoded else b''
            else:
                raise ValueError()
            if len(key) != 32:
                raise ValueError()
            return key
        except VaultLockedError:
            raise
        except Exception:
            raise VaultLockedError('The protected encryption key could not be unlocked') from None

    def _create_key(self) -> bytes:
        key = secrets.token_bytes(32)
        record = {'version': 1}
        if self._passphrase:
            salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
            wrapped = AESGCM(_derive(self._passphrase, salt)).encrypt(nonce, key, _KEY_AAD)
            record.update(mode='passphrase', salt=base64.b64encode(salt).decode(),
                          nonce=base64.b64encode(nonce).decode(), wrapped=base64.b64encode(wrapped).decode())
        elif sys.platform == 'win32':
            record.update(mode='dpapi', wrapped=base64.b64encode(_dpapi(key, decrypt=False)).decode())
        else:
            backend = _keyring()
            if backend is None:
                raise VaultLockedError('Configure a secure OS keyring or supply a vault passphrase')
            account = str(uuid.uuid4())
            try:
                backend.set_password(_SERVICE, account, base64.b64encode(key).decode())
            except Exception:
                raise VaultLockedError('The secure OS keyring is unavailable') from None
            record.update(mode='keyring', account=account)
        # Exclusive interprocess lock and atomic replace prevent partially written keys.
        temporary = self.data_dir / ('.protected-key-' + uuid.uuid4().hex)
        try:
            descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(record, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.key_path)
            private_file(self.key_path)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    @staticmethod
    def _aad(purpose: str) -> bytes:
        if not isinstance(purpose, str) or not purpose or len(purpose) > 1024:
            raise ValueError('An encryption purpose is required')
        return b'phonedex:data:v1:' + purpose.encode('utf-8')

    def encrypt(self, value: str, purpose: str) -> str:
        if not isinstance(value, str):
            raise ValueError('Encrypted values must be text')
        aad = self._aad(purpose)
        with self._exclusive():
            key = self._read_key(create=True)
            nonce = secrets.token_bytes(12)
            encrypted = AESGCM(key).encrypt(nonce, value.encode('utf-8'), aad)
        return 'v1:' + base64.urlsafe_b64encode(nonce + encrypted).decode('ascii')

    def decrypt(self, blob: str, purpose: str) -> str:
        aad = self._aad(purpose)
        with self._exclusive():
            key = self._read_key(create=False)
            try:
                if not isinstance(blob, str) or not blob.startswith('v1:'):
                    raise ValueError()
                raw = base64.b64decode(blob[3:], altchars=b'-_', validate=True)
                if len(raw) < 28:
                    raise ValueError()
                return AESGCM(key).decrypt(raw[:12], raw[12:], aad).decode('utf-8')
            except (ValueError, InvalidTag, UnicodeError):
                raise VaultIntegrityError('Encrypted data failed authentication') from None

    def destroy(self) -> None:
        """Remove the protected data key; original backups can still retain it."""
        with self._exclusive():
            if not self.key_path.exists():
                return
            try:
                record = json.loads(self.key_path.read_text(encoding='utf-8'))
                if record.get('mode') == 'keyring':
                    backend = _keyring()
                    if backend is None:
                        raise VaultLockedError('Unlock the OS keyring to erase this vault')
                    backend.delete_password(_SERVICE, record['account'])
            except VaultLockedError:
                raise
            except Exception:
                raise VaultLockedError('The protected key could not be erased') from None
            # Best effort overwrite of wrapped material, then removal. Filesystem/SSD
            # snapshots require independent deletion; no secure-media promise is made.
            with self.key_path.open('r+b') as handle:
                handle.write(secrets.token_bytes(self.key_path.stat().st_size))
                handle.flush()
                os.fsync(handle.fileno())
            self.key_path.unlink()
