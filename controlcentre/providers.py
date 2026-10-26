"""Local-only configuration checks reuse PhoneDex's onboarding validators."""
import json

from fastapi import HTTPException


CONFIG_KEYS = {'termination_uri', 'public_ip', 'caller_id', 'allowed_recipients', 'gateway_url',
               'media_port', 'media_port_forwarded', 'max_calls_per_day', 'region', 'transport', 'cache_transcripts'}


def validate_config(config, kind='sip', *, require_complete=True):
    from onboarding import sip_target, public_ipv4, e164, approved_recipients, worker_endpoint
    validators = dict(termination_uri=sip_target, public_ip=public_ipv4, caller_id=e164,
                      allowed_recipients=approved_recipients, gateway_url=worker_endpoint)
    required = ({'gateway_url'} if kind == 'worker' else set() if kind == 'openai' else
                {'termination_uri', 'public_ip', 'caller_id'})
    errors = [key for key in sorted(required) if require_complete and not config.get(key)]
    for key, value in config.items():
        if key not in CONFIG_KEYS:
            errors.append(key)
        elif key in validators:
            try:
                validators[key](str(value))
            except (TypeError, ValueError):
                errors.append(key)
        elif key == 'media_port':
            try:
                valid = not isinstance(value, bool) and 1024 <= int(value) <= 65534 and int(value) % 2 == 0
            except (ValueError, TypeError):
                valid = False
            if not valid:
                errors.append(key)
        elif key == 'max_calls_per_day':
            try:
                valid = not isinstance(value, bool) and 1 <= int(value) <= 1000
            except (ValueError, TypeError):
                valid = False
            if not valid:
                errors.append(key)
        elif key=='cache_transcripts' and type(value) is not bool:
            errors.append(key)
    return dict(valid=not errors, checked_locally=True, network_verified=False,
                errors=[{'field': key, 'message': 'Missing, invalid or unsupported configuration field'} for key in dict.fromkeys(errors)])


def public_provider(item):
    return {k: v for k, v in item.items() if k not in {'credentials', 'credentials_encrypted'}}


def prepare_provider(item, vault, existing=None):
    data = dict(item)
    credentials = data.pop('credentials', {})
    if set(data.get('config', {})) - CONFIG_KEYS:
        raise HTTPException(422, 'Unsupported provider configuration field')
    if not validate_config(data.get('config', {}), data.get('kind', 'sip'), require_complete=False)['valid']:
        raise HTTPException(422, 'Invalid provider configuration')
    if credentials:
        from onboarding import checked_field
        try:
            for value in credentials.values():
                if value:
                    checked_field(value)
        except (TypeError, ValueError):
            raise HTTPException(422, 'Invalid provider credentials') from None
        credentials = {key: value for key, value in credentials.items() if value}
        if existing and existing.get('credentials_encrypted'):
            try:
                previous = json.loads(vault.decrypt(existing['credentials_encrypted'], 'provider:' + data['id']))
                credentials = {**previous, **credentials}
            except Exception:
                raise HTTPException(409, 'Secure credential storage is unavailable') from None
        try:
            data['credentials_encrypted'] = vault.encrypt(json.dumps(credentials), 'provider:' + data['id'])
        except Exception:
            raise HTTPException(409, 'Secure credential storage is unavailable') from None
        data['credential_presence'] = {key: bool(value) for key, value in credentials.items()}
    elif existing:
        for key in ('credentials_encrypted', 'credential_presence'):
            if key in existing:
                data[key] = existing[key]
    else:
        data['credential_presence'] = {}
    return data
