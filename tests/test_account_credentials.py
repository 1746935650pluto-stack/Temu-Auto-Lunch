"""No production .env, credentials, remote API or databases are used here."""
import codecs
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import account_credentials
import server
import test_collection_accounts as fixtures


class CredentialFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / '.env'
        self.values = account_credentials.credential_values(3, 'test-key-3', 'test-secret-3')

    def test_preserves_bom_crlf_comments_unrelated_settings_and_removes_duplicate_keys(self):
        original = codecs.BOM_UTF8 + b'# keep\r\nAPP_MODE=live\r\nMIAOSHOU_APP_KEY=one\r\nMIAOSHOU_ACCOUNT_3_APP_KEY=\r\nMIAOSHOU_ACCOUNT_3_APP_KEY=\r\nOTHER="two"'
        self.path.write_bytes(original)
        account_credentials.persist_credentials(self.path, self.values)
        result = self.path.read_bytes()
        self.assertTrue(result.startswith(codecs.BOM_UTF8 + b'# keep\r\nAPP_MODE=live\r\nMIAOSHOU_APP_KEY=one\r\n'))
        self.assertIn(b'OTHER="two"\r\n', result)
        self.assertEqual(result.count(b'MIAOSHOU_ACCOUNT_3_APP_KEY='), 1)
        self.assertNotIn(b'\n', result.replace(b'\r\n', b''))
        with patch.object(server, 'DATA_ROOT', self.path.parent), patch.dict(os.environ, {}, clear=True):
            server.load_local_env()
            self.assertEqual(os.getenv('MIAOSHOU_ACCOUNT_3_APP_SECRET'), 'test-secret-3')
            self.assertEqual(os.getenv('APP_MODE'), 'live')
        with patch.object(account_credentials.os, 'replace') as replace:
            account_credentials.persist_credentials(self.path, self.values)
            replace.assert_not_called()

    def test_replace_failure_keeps_original_and_removes_secret_tempfile(self):
        self.path.write_bytes(b'APP_MODE=demo\n')
        with patch.object(account_credentials.os, 'replace', side_effect=PermissionError('synthetic')):
            with self.assertRaises(PermissionError):
                account_credentials.persist_credentials(self.path, self.values)
        self.assertEqual(self.path.read_bytes(), b'APP_MODE=demo\n')
        self.assertEqual(list(self.path.parent.glob('.env-setup-*')), [])

    def test_manual_edit_and_different_disk_credentials_are_not_overwritten(self):
        self.path.write_bytes(b'MIAOSHOU_ACCOUNT_3_APP_KEY=existing\n')
        with self.assertRaises(account_credentials.CredentialConfigError):
            account_credentials.persist_credentials(self.path, self.values)
        self.assertEqual(self.path.read_bytes(), b'MIAOSHOU_ACCOUNT_3_APP_KEY=existing\n')
        self.path.write_bytes(b'APP_MODE=demo\n')
        def edit(_descriptor):
            self.path.write_bytes(b'APP_MODE=live\n')
        with patch.object(account_credentials.os, 'fsync', side_effect=edit):
            with self.assertRaises(account_credentials.CredentialConfigError):
                account_credentials.persist_credentials(self.path, self.values)
        self.assertEqual(self.path.read_bytes(), b'APP_MODE=live\n')
        self.assertEqual(list(self.path.parent.glob('.env-setup-*')), [])

    def test_validation_rejects_injection_and_unsupported_ids(self):
        for value in ('', ' ', 'x\nAPP_MODE=live', 'x y', 'x"', 'x\x00', '$TOKEN', 'x#', 'x' * 513, None, 12):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(account_credentials.CredentialConfigError):
                    account_credentials.credential_values(3, value, 'test-secret')
        for account_id in (True, '3', 1, 0, 21):
            with self.assertRaises(account_credentials.CredentialConfigError):
                account_credentials.credential_values(account_id, 'key', 'secret')

    def test_collection_name_is_atomic_unicode_safe_and_clears_on_account_removal(self):
        self.path.write_bytes(codecs.BOM_UTF8 + b'# keep\r\nMIAOSHOU_ACCOUNT_4_NAME=old\r\nOTHER=ok\r\n')
        self.assertEqual(account_credentials.persist_collection_name(self.path, 4, '  美国  采集箱  '), '美国 采集箱')
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))
        self.assertIn('MIAOSHOU_ACCOUNT_4_NAME=美国 采集箱\r\n'.encode(), data)
        self.assertEqual(data.count(b'MIAOSHOU_ACCOUNT_4_NAME='), 1)
        account_credentials.remove_credentials(self.path, 4)
        self.assertNotIn(b'MIAOSHOU_ACCOUNT_4_NAME=', self.path.read_bytes())
        for account, name in ((0, 'x'), (21, 'x'), (1, ''), (1, 'x\nAPP_MODE=live'),
                              (1, 'x=y'), (1, 'x#y'), (1, 'x' * 41), (1, None)):
            with self.assertRaises(account_credentials.CredentialConfigError):
                account_credentials.collection_name_value(account, name)


class CredentialRouteTests(unittest.TestCase):
    request = fixtures.CollectionAccountTests.request
    stop_server = fixtures.CollectionAccountTests.stop_server

    def setUp(self):
        fixtures.CollectionAccountTests.setUp(self)
        self.root = server.ORDER_SYNC_STORE.path.parent
        mocked = patch.object(server, 'DATA_ROOT', self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.path = self.root / '.env'
        self.original = b'# preserved\nAPP_MODE=live\nMIAOSHOU_APP_KEY=test-one\nMIAOSHOU_APP_SECRET=secret-one\n'
        self.path.write_bytes(self.original)
        self.account_id, _ = server.ACCOUNT_SLOT_STORE.add(server.configured_miaoshou_account_slots())
        self.assertEqual(self.account_id, 3)
        self.url = '/api/miaoshou-accounts/3/credentials'
        self.body = {'appKey': 'test-key-3', 'appSecret': 'test-secret-3', 'confirmation': 'SAVE_COLLECTION_API_CREDENTIALS'}
        self.headers = {'Origin': f'http://127.0.0.1:{self.httpd.server_port}'}

    def save(self, body=None, headers=None, url=None):
        return self.request('POST', url or self.url, self.body if body is None else body,
                            self.headers if headers is None else headers)

    def test_save_activates_only_target_without_remote_calls_or_mode_change_and_is_idempotent(self):
        for _ in range(2):
            status, result = self.save()
            self.assertEqual(status, 200, result)
            self.assertTrue(result['data']['configured'])
            self.assertNotIn('test-secret-3', str(result))
            self.assertNotIn('test-key-3', str(result))
        self.assertTrue(self.path.read_bytes().startswith(self.original))
        self.assertEqual(server.MiaoShouClient(3).app_key, 'test-key-3')
        self.assertEqual(server.MiaoShouClient(1).app_key, 'test-one')
        self.assertEqual(server.MiaoShouClient(2).app_key, 'test-two')
        self.assertEqual(os.getenv('APP_MODE'), 'live')
        self.assertEqual(self.calls, [])
        metadata = self.request('GET', '/api/miaoshou-accounts')[1]
        self.assertNotIn('test-secret', str(metadata))
        self.assertNotIn('test-key', str(metadata))
        health = self.request('GET', '/api/health', headers={'X-Miaoshou-Account-Id': '3'})[1]
        self.assertTrue(health['miaoShouConfigured'])
        self.assertTrue(health['accountCredentialSetup'])

    def test_origin_host_confirmation_and_json_required(self):
        for headers in ({}, {'Origin': 'http://evil.invalid'}, {**self.headers, 'Host': 'evil.invalid'},
                        {**self.headers, 'Content-Type': 'text/plain'}):
            self.assertIn(self.save(headers=headers)[0], (400, 403))
        self.assertEqual(self.save(body={**self.body, 'confirmation': 'wrong'})[0], 409)
        self.assertEqual(self.save(body={**self.body, 'APP_MODE': 'live'})[0], 409)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(server.MiaoShouClient(3).configured)

    def test_existing_credentials_and_primary_unknown_deleted_historical_accounts_are_protected(self):
        for account_id in (1, 2, 9):
            self.assertEqual(self.save(url=f'/api/miaoshou-accounts/{account_id}/credentials')[0], 409)
        server.order_store(3).upsert([{'packageId': 7, 'orderNo': 'history'}])
        self.assertEqual(self.save()[0], 409)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.calls, [])
        # Slot 4 is truly empty; a deleted entry must not be resurrected by save.
        account_id, _ = server.ACCOUNT_SLOT_STORE.add(server.configured_miaoshou_account_slots())
        server.ACCOUNT_SLOT_STORE.delete(account_id, server.miaoshou_account_slots())
        self.assertEqual(self.save(url=f'/api/miaoshou-accounts/{account_id}/credentials')[0], 409)

    def test_failed_write_does_not_activate_and_error_has_no_secrets(self):
        with patch.object(account_credentials.os, 'replace', side_effect=PermissionError('test-secret-3')):
            status, body = self.save()
        self.assertEqual(status, 500)
        self.assertNotIn('test-secret-3', str(body))
        self.assertFalse(server.MiaoShouClient(3).configured)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob('.env-setup-*')), [])

    def test_parallel_saves_preserve_both_accounts_and_competing_replacement_is_rejected(self):
        server.ACCOUNT_SLOT_STORE.add(server.configured_miaoshou_account_slots())
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda account: self.save(
                url=f'/api/miaoshou-accounts/{account}/credentials',
                body={**self.body, 'appKey': f'test-key-{account}', 'appSecret': f'test-secret-{account}'}), (3, 4)))
        self.assertEqual([response[0] for response in responses], [200, 200])
        for account in (3, 4):
            self.assertIn(f'MIAOSHOU_ACCOUNT_{account}_APP_KEY=test-key-{account}'.encode(), self.path.read_bytes())
        before = self.path.read_bytes()
        self.assertEqual(self.save(body={**self.body, 'appSecret': 'different'})[0], 409)
        self.assertEqual(self.path.read_bytes(), before)

    def test_configuration_in_demo_does_not_enable_writes(self):
        with patch.dict(os.environ, {'APP_MODE': 'demo'}):
            self.assertEqual(self.save()[0], 200)
            self.assertEqual(os.getenv('APP_MODE'), 'demo')
            response = self.request('POST', '/api/item/create', {'confirmation': 'CREATE_MIAOSHOU_ITEM'},
                                    {'X-Miaoshou-Account-Id': '3'})
            self.assertEqual(response[0], 409)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
