#!/usr/bin/env python3
"""Fetch existing application secrets into root-only tmpfs, never VM metadata."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen


def get_json(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=20) as response:
        return json.load(response)


def environment_text(environment):
    for key, value in environment.items():
        if not key.replace("_", "a").isalnum() or any(c in str(value) for c in "\r\n\0"):
            raise ValueError("Invalid environment entry")
    return "".join(f"{key}={value}\n" for key, value in environment.items())


def main():
    os.umask(0o077)
    config = json.loads(Path('/etc/pacers/config.json').read_text())
    runtime = Path('/run/pacers')
    runtime.mkdir(mode=0o700, exist_ok=True)
    token = get_json(
        'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
        {'Metadata-Flavor': 'Google'},
    )['access_token']
    environment = dict(config['environment'])
    for secret in config['secrets']:
        payload = get_json(
            'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/'
            + secret['id'] + '/payload?versionId=' + secret['version_id'],
            {'Authorization': 'Bearer ' + token},
        )
        entry = next(e for e in payload['entries'] if e['key'] == secret['key'])
        environment[secret['environment_variable']] = entry['textValue']
    env_path = runtime / 'app.env'
    env_path.write_text(environment_text(environment))
    env_path.chmod(0o600)
    # Do not leave the IAM token in Docker's persistent credential store.
    with tempfile.TemporaryDirectory(dir=runtime, prefix='docker-') as directory:
        command = ['docker', '--config', directory]
        subprocess.run(command + ['login', '-u', 'iam', '--password-stdin', 'cr.yandex'],
                       input=token, text=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        subprocess.run(command + ['pull', config['image']], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    # Unprivileged application containers must not obtain the VM's IAM token.
    rule = ['DOCKER-USER', '-d', '169.254.169.254/32', '-j', 'REJECT']
    if subprocess.run(['iptables', '-C'] + rule, capture_output=True).returncode:
        subprocess.run(['iptables', '-I'] + rule, check=True)
    (runtime / 'image').write_text(config['image'])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Runtime preparation failed; credentials and response bodies suppressed.', file=sys.stderr)
        sys.exit(1)
