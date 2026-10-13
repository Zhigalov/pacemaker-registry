"""Safety checks for the temporary recovery configuration (no cloud requests)."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / 'ops' / 'temporary-vm'
spec = importlib.util.spec_from_file_location('temporary_vm_prepare', ROOT / 'prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_environment_keeps_values_literal():
    assert prepare.environment_text({'DATABASE_PASSWORD': 'a$b=c#d'}) == 'DATABASE_PASSWORD=a$b=c#d\n'


@pytest.mark.parametrize('environment', [
    {'X': 'a\nINJECTED=yes'}, {'X': 'a\r'}, {'X': 'a\0'}, {'X=Y': 'a'},
])
def test_environment_rejects_injection(environment):
    with pytest.raises(ValueError):
        prepare.environment_text(environment)


def test_container_is_private_and_hardened():
    unit = (ROOT / 'pacers.service').read_text()
    assert '--publish 127.0.0.1:8080:8080' in unit
    assert '--read-only' in unit
    assert '--cap-drop=ALL' in unit
    assert '--security-opt=no-new-privileges' in unit
    assert '--env-file /run/pacers/app.env' in unit
    assert 'Restart=always' in unit


def test_no_passwords_in_boot_metadata_and_tls_enabled():
    cloud_init = (ROOT / 'cloud-init.yaml').read_text()
    assert 'DATABASE_PASSWORD' not in cloud_init
    assert 'ssh_pwauth: false' in cloud_init
    nginx = (ROOT / 'nginx.conf').read_text()
    assert 'return 301 https://pacersreg.ru$request_uri' in nginx
    assert 'listen 443 ssl' in nginx
    assert 'ssl_protocols TLSv1.2 TLSv1.3' in nginx


def test_dns_normalization_and_exact_record_arguments():
    dns_spec = importlib.util.spec_from_file_location('recovery_dns', ROOT / 'switch_dns.py')
    dns = importlib.util.module_from_spec(dns_spec)
    dns_spec.loader.exec_module(dns)
    record = {'name': 'pacersreg.ru.', 'type': 'A', 'ttl': '60', 'data': ['192.0.2.1']}
    assert dns.normalize([record]) == dns.normalize([dict(record, ttl=60)])
    assert dns.record_argument(record) == 'pacersreg.ru. 60 A 192.0.2.1'
    with pytest.raises(ValueError):
        dns.record_argument(dict(record, data=['192.0.2.1', '192.0.2.2']))
