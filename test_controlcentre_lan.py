from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from controlcentre.app import create_app


def test_lan_requires_explicit_private_host_and_subnet(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path,tmp_path,lan_host='0.0.0.0',lan_subnet='0.0.0.0/0')


def test_lan_tls_auth_host_origin_and_no_remote_first_setup(tmp_path):
    app=create_app(tmp_path,tmp_path,lan_host='192.168.4.144',lan_subnet='192.168.4.0/22')
    remote=TestClient(app,base_url='https://192.168.4.144:8808',client=('192.168.4.42',1234))
    assert remote.get('/api/auth/status').status_code==200
    assert remote.get('/api/workspace').status_code==401
    assert remote.post('/api/auth/setup',json={'password':'synthetic-lan-test-password'}).status_code==403
    local=TestClient(app,base_url='https://192.168.4.144:8808',client=('127.0.0.1',1234))
    assert local.post('/api/auth/setup',json={'password':'synthetic-lan-test-password'}).status_code==200
    login=remote.post('/api/auth/login',json={'password':'synthetic-lan-test-password'})
    assert login.status_code==200
    assert 'Secure' in login.headers['set-cookie']
    assert remote.get('/api/workspace').status_code==200
    assert remote.get('/api/auth/status',headers={'host':'evil.test'}).status_code==403
    assert remote.post('/api/contacts',headers={'origin':'https://evil.test'},json={}).status_code==403
    assert remote.post('/api/contacts',json={}).status_code==403
    outsider=TestClient(app,base_url='https://192.168.4.144:8808',client=('203.0.113.5',1234))
    assert outsider.get('/api/auth/status').status_code==403
    insecure=TestClient(app,base_url='http://192.168.4.144:8808',client=('192.168.4.42',1234))
    assert insecure.get('/api/auth/status').status_code==403


def test_lan_certificate_contains_only_expected_addresses(tmp_path):
    from cryptography import x509
    from controlcentre.lan import local_certificate
    cert,key=local_certificate(tmp_path,'192.168.4.144')
    certificate=x509.load_pem_x509_certificate(cert.read_bytes())
    names=certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert str(names.get_values_for_type(x509.IPAddress)[0])=='192.168.4.144'
    assert key.is_file()
