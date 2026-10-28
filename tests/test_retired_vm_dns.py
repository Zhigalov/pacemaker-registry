"""Regression checks: DNS rollback must never use a released public IP."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'temporary_vm_dns', Path(__file__).resolve().parents[1] / 'ops/temporary-vm/switch_dns.py')
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


class TemporaryVmDnsTests(unittest.TestCase):
    def test_deleted_vm_is_blocked_before_cloud_read_or_write(self):
        for flags in ([], ['--apply']):
            with self.subTest(flags=flags), \
                 patch('sys.argv', ['switch_dns.py', '--to', 'vm', *flags]), \
                 patch.object(dns.Path, 'read_text', return_value=json.dumps(
                     {'current_runtime': {'vm_status': 'DELETED'}})), \
                 patch.object(dns, 'yc') as cloud:
                with self.assertRaisesRegex(RuntimeError, 'released address'):
                    dns.main()
                cloud.assert_not_called()

    def test_serverless_target_remains_available_after_vm_deletion(self):
        original = [{'name': 'pacersreg.ru.', 'type': 'ANAME', 'ttl': '300', 'data': ['gateway.example.']}]
        config = {'current_runtime': {'vm_status': 'DELETED'}, 'original_dns': original,
                  'public_ip': '192.0.2.1', 'dns_zone_id': 'test-zone'}
        with patch('sys.argv', ['switch_dns.py', '--to', 'serverless', '--apply']), \
             patch.object(dns.Path, 'read_text', return_value=json.dumps(config)), \
             patch.object(dns, 'yc', return_value={'record_sets': original}) as cloud, \
             contextlib.redirect_stdout(io.StringIO()):
            dns.main()
        cloud.assert_called_once_with(['dns', 'zone', 'list-records', 'test-zone'])


if __name__ == '__main__':
    unittest.main()
