"""Bounded transport for PhoneDex's existing operator API, never carrier APIs."""
from __future__ import annotations

import json
import re
import ssl
from urllib.parse import urlsplit

import httpx

CALL_ID = re.compile(r'^call_[a-f0-9]{32}$')
COMMAND_ID = re.compile(r'^cmd_[a-f0-9]{32}$')


class BackendError(RuntimeError):
    """Fixed diagnostic labels; upstream bodies and credentials never escape."""


class OperatorClient:
    def __init__(self, provider, cipher, *, transport=None):
        base = provider.get('config', {}).get('gateway_url', '').rstrip('/')
        parsed = urlsplit(base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                parsed.path != '/agent/gateway'):
            raise BackendError('invalid_configuration')
        self.base = base.removesuffix('/gateway')
        try:
            credentials = json.loads(cipher.decrypt(provider['credentials_encrypted'], 'provider:' + provider['id']))
            token = credentials.get('token') or credentials.get('api_key')
            if not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(c.isspace() for c in token):
                raise ValueError()
        except Exception:
            raise BackendError('credentials_unavailable') from None
        self.http = httpx.Client(headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
            timeout=8, follow_redirects=False, trust_env=False, transport=transport,
            verify=ssl.create_default_context())

    def close(self):
        self.http.close()

    def request(self, method, path='', payload=None, *, missing_ok=False):
        # Callers select only protocol paths; IDs cannot redirect or inject paths.
        if path not in {'', '/calls', '/events', '/inbound/events'}:
            match = re.fullmatch(r'/calls/(call_[a-f0-9]{32})(/(transcript|commands|steers|hangup))?', path)
            if not match:
                raise BackendError('invalid_path')
        try:
            with self.http.stream(method, self.base + path, json=payload if payload is not None else None) as response:
                if response.status_code == 404 and missing_ok:
                    return None
                if response.status_code in {401, 403}:
                    raise BackendError('authentication_failed')
                if not 200 <= response.status_code < 300:
                    raise BackendError('upstream_unavailable')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 512 * 1024:
                        raise BackendError('response_too_large')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise BackendError('invalid_response')
                return value
        except BackendError:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise BackendError('transport_unavailable') from None
