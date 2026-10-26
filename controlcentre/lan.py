"""Explicit private-subnet TLS access. No tunnel or public listener."""
from datetime import datetime,timedelta,timezone
import ipaddress
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from .crypto import private_directory,private_file

PRIVATE_NETWORKS=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def validate_lan(host,subnet):
    address=ipaddress.ip_address(host)
    network=ipaddress.ip_network(subnet,strict=False)
    if (address.version!=4 or not any(address in n and network.subnet_of(n) for n in PRIVATE_NETWORKS)
            or address not in network or network.prefixlen<16):
        raise ValueError('LAN mode requires a specific private IPv4 address and a restricted private subnet')
    return address,network


def local_certificate(data_dir,host):
    folder=private_directory(Path(data_dir)/'lan-tls')
    cert,key=folder/(host+'.crt'),folder/(host+'.key')
    if cert.is_file() and key.is_file():
        private_file(cert);private_file(key)
        existing=x509.load_pem_x509_certificate(cert.read_bytes())
        if existing.not_valid_after_utc>datetime.now(timezone.utc)+timedelta(days=1):return cert,key
    secret=ec.generate_private_key(ec.SECP256R1())
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PhoneDex local dashboard')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(secret.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host)),
             x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.DNSName('localhost')]),critical=False)
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).sign(secret,hashes.SHA256()))
    private_file(key,create=True);private_file(cert,create=True)
    key.write_bytes(secret.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert,key
