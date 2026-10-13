#!/usr/bin/env python3
"""Install the existing release on a verified SSH host without logging secrets.

No provisioning or DNS mutation. Requires an already provisioned VM, a verified
known_hosts entry and the operator's existing SSH identity/agent.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

REMOTE_INSTALL = r'''
import json, os, pathlib, sys
os.umask(0o077)
payload = json.load(sys.stdin)
allowed = {
    '/etc/pacers/config.json', '/etc/pacers/tls/fullchain.pem',
    '/etc/pacers/tls/privkey.pem', '/opt/pacers/prepare.py',
    '/etc/systemd/system/pacers.service', '/etc/nginx/sites-available/pacers',
}
assert set(payload) == allowed
for target, content in payload.items():
    path = pathlib.Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600 if target.startswith('/etc/pacers/') else 0o644)
default = pathlib.Path('/etc/nginx/sites-enabled/default')
if default.is_symlink():
    default.unlink()
link = pathlib.Path('/etc/nginx/sites-enabled/pacers')
if not link.exists():
    link.symlink_to('/etc/nginx/sites-available/pacers')
print('Installed runtime configuration and TLS; secrets suppressed.')
'''


def yc_json(arguments):
    result = subprocess.run(['yc', *arguments, '--format', 'json'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Cloud request failed; output suppressed')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--certificate', required=True)
    args = parser.parse_args()
    revision = yc_json(['serverless', 'container', 'revision', 'get', args.revision])
    certificate = yc_json(['certificate-manager', 'certificate', 'content', args.certificate])
    image = revision['image']
    environment = image['environment']
    if 'DATABASE_PASSWORD' in environment:
        raise RuntimeError('Expected a Lockbox reference, not an inline password')
    config = {
        'image': image['image_url'].split(':')[0] + '@' + image['image_digest'],
        'environment': environment,
        'secrets': revision['secrets'],
    }
    chain = certificate['certificate_chain']
    if isinstance(chain, list):
        chain = '\n'.join(chain)
    payload = {
        '/etc/pacers/config.json': json.dumps(config),
        '/etc/pacers/tls/fullchain.pem': chain,
        '/etc/pacers/tls/privkey.pem': certificate['private_key'],
        '/opt/pacers/prepare.py': (ROOT / 'prepare.py').read_text(),
        '/etc/systemd/system/pacers.service': (ROOT / 'pacers.service').read_text(),
        '/etc/nginx/sites-available/pacers': (ROOT / 'nginx.conf').read_text(),
    }
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'UserKnownHostsFile=' + args.known_hosts, '-o', 'ConnectTimeout=10',
           'yc-user@' + args.host]
    result = subprocess.run(ssh + ['sudo python3 -c ' + shlex.quote(REMOTE_INSTALL)],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('SSH installation failed; output suppressed')
    print('Runtime and certificate installed', flush=True)
    command = ('sudo nginx -t && sudo systemctl daemon-reload && '
               'sudo systemctl enable pacers nginx && sudo systemctl restart pacers && '
               'sudo systemctl restart nginx')
    result = subprocess.run(ssh + [command], capture_output=True, text=True, timeout=360)
    if result.returncode:
        raise RuntimeError('Service startup failed; inspect redacted status')
    print('Application and HTTPS proxy started', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': installation failed; sensitive output suppressed.', file=sys.stderr)
        sys.exit(1)
