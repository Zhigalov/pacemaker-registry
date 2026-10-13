#!/usr/bin/env python3
"""Explicit, guarded DNS cutover/rollback; leaves ACME, NS and SOA unchanged."""
import argparse
import json
from pathlib import Path
import subprocess


def yc(arguments):
    result = subprocess.run(['yc', *arguments, '--format', 'json'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Cloud DNS operation failed; inspect state before retrying')
    return json.loads(result.stdout)


def normalize(records):
    return sorted((r['name'], r['type'], int(r['ttl']), tuple(sorted(r['data']))) for r in records)


def record_argument(record):
    if len(record['data']) != 1:
        raise ValueError('Only single-value recovery records are supported')
    return f"{record['name']} {record['ttl']} {record['type']} {record['data'][0]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--to', choices=['vm', 'serverless'], required=True)
    parser.add_argument('--apply', action='store_true', help='Without this flag, only inspect/plan')
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name('resources.json').read_text())
    original = config['original_dns']
    vm = [dict(name=r['name'], type='A', ttl='60', data=[config['public_ip']]) for r in original]
    expected, desired = (original, vm) if args.to == 'vm' else (vm, original)
    names = {r['name'] for r in original}

    def live():
        records = yc(['dns', 'zone', 'list-records', config['dns_zone_id']])['record_sets']
        return [r for r in records if r['name'] in names and r['type'] in ['A', 'AAAA', 'ANAME', 'CNAME']]

    current = live()
    if normalize(current) == normalize(desired):
        print('DNS already points to', args.to)
        return
    if normalize(current) != normalize(expected):
        raise RuntimeError('DNS differs from expected source; refusing to overwrite concurrent changes')
    print('Verified source; target:', args.to)
    if not args.apply:
        print('Dry run only; use --apply after health and TLS checks')
        return
    command = ['dns', 'zone', 'update-records', config['dns_zone_id']]
    for record in expected:
        command += ['--delete-record', record_argument(record)]
    for record in desired:
        command += ['--add-record', record_argument(record)]
    yc(command)
    if normalize(live()) != normalize(desired):
        raise RuntimeError('DNS postcondition failed; inspect state, do not retry blindly')
    print('DNS cutover verified:', args.to)


if __name__ == '__main__':
    main()
