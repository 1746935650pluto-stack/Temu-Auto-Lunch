"""Settings lifecycle tests: temporary .env/SQLite, synthetic keys, no network."""
import codecs
from contextlib import closing
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import account_credentials
import server
import test_account_credentials as fixtures

REAL_VENDOR_POST = server.MiaoShouClient.post


class IntegrationSettingsTests(unittest.TestCase):
    request = fixtures.CredentialRouteTests.request
    stop_server = fixtures.CredentialRouteTests.stop_server

    def setUp(self):
        fixtures.CredentialRouteTests.setUp(self)
        for name, value in (('SHARED_SERVICES', {}), ('ACCOUNT_MUTATIONS_ACTIVE', 0),
                            ('ACCOUNT_VENDOR_ACTIVE', {}), ('DEEPSEEK_REQUESTS_ACTIVE', 0),
                            ('DEEPSEEK_ACCOUNT_ACTIVE', {})):
            mocked = patch.object(server, name, value)
            mocked.start(); self.addCleanup(mocked.stop)
        server.save_collection_credentials(3, 'test-key-3', 'test-secret-3')
        self.service = server.shared_automation()
        self.service.accounts()

    def remove(self, account=3, body=None, headers=None):
        return self.request('POST', f'/api/miaoshou-accounts/{account}/remove',
                            {'confirmation': 'REMOVE_COLLECTION_ACCOUNT_AND_HISTORY'} if body is None else body,
                            self.headers if headers is None else headers)

    def deepseek(self, key='test-ai-key', remove=False, headers=None):
        body = {'confirmation': 'REMOVE_DEEPSEEK_API'} if remove else {'confirmation': 'SAVE_DEEPSEEK_API', 'apiKey': key}
        return self.request('POST', '/api/settings/deepseek', body, self.headers if headers is None else headers)

    def rename(self, account=3, name='北美采集箱', body=None, headers=None):
        request = {'name': name, 'confirmation': 'RENAME_COLLECTION_ACCOUNT'} if body is None else body
        return self.request('POST', f'/api/miaoshou-accounts/{account}/name', request,
                            self.headers if headers is None else headers)

    def test_rename_updates_only_local_label_and_keeps_credentials_jobs_and_numbers(self):
        pool = server.automation_pool(3)
        pool.put_item(123, 'site', 0, {'title': 'unchanged'})
        job = pool.start_job('a' * 32, 123, 7, 'US')
        status, body = self.rename()
        self.assertEqual(status, 200, body)
        self.assertEqual(body['data']['name'], '北美采集箱')
        self.assertEqual(next(row['name'] for row in body['data']['accounts'] if row['id'] == 3), '北美采集箱')
        self.assertIn('MIAOSHOU_ACCOUNT_3_NAME=北美采集箱', self.path.read_text(encoding='utf-8'))
        self.assertEqual(server.MiaoShouClient(3).app_key, 'test-key-3')
        self.assertEqual(pool.get_job(job['jobId'])['detailId'], 123)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rename(1, '默认箱')[0], 200)

    def test_settings_refreshes_miaoshou_shops_warehouses_and_freight_templates(self):
        self.calls.clear()
        status, body = self.request('POST', '/api/settings/miaoshou-shops',
                                    {'confirmation': 'REFRESH_MIAOSHOU_SHOPS'}, self.headers)
        self.assertEqual(status, 200, body)
        refreshed = body['data']
        self.assertEqual({row['accountId'] for row in refreshed['accounts']}, {1, 2, 3})
        self.assertTrue(all(row['shopCount'] == 1 for row in refreshed['accounts']))
        self.assertTrue(all(row['syncedShopCount'] == 1 for row in refreshed['accounts']))
        self.assertTrue(all(row['freightTemplateCount'] == 1 for row in refreshed['accounts']))
        self.assertTrue(all(row['warehouseCount'] == 1 for row in refreshed['accounts']))
        self.assertEqual(refreshed['errors'], [])
        self.assertEqual(refreshed['warnings'], [])
        self.assertEqual({path for _, path, _ in self.calls}, {server.SHOP_LIST, server.HALF_FREIGHT, server.HALF_WAREHOUSE})
        self.assertEqual(sum(path == server.HALF_FREIGHT for _, path, _ in self.calls), 3)
        self.assertEqual(sum(path == server.HALF_WAREHOUSE for _, path, _ in self.calls), 3)
        self.assertTrue(all(payload == {'platform': 'pddkjChoice', 'site': 'PDDKJCHOICE', 'pageNo': 1, 'pageSize': 100}
                            for _, path, payload in self.calls if path == server.SHOP_LIST))
        self.assertTrue(all(payload == {'shopId': 2, 'isRsync': 1}
                            for _, path, payload in self.calls if path in {server.HALF_FREIGHT, server.HALF_WAREHOUSE}))
        self.assertEqual(self.request('POST', '/api/settings/miaoshou-shops', {}, self.headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/settings/miaoshou-shops',
                                      {'confirmation': 'REFRESH_MIAOSHOU_SHOPS'}, {})[0], 403)

    def test_rename_requires_same_origin_exact_fields_valid_name_and_active_account(self):
        before = self.path.read_bytes()
        for body in ({}, {'name': 'x'}, {'name': 'x', 'confirmation': 'wrong'},
                     {'name': 'x=y', 'confirmation': 'RENAME_COLLECTION_ACCOUNT'},
                     {'name': 'x', 'confirmation': 'RENAME_COLLECTION_ACCOUNT', 'extra': 1}):
            self.assertIn(self.rename(body=body)[0], (400, 409))
        self.assertEqual(self.rename(headers={})[0], 403)
        self.assertEqual(self.remove()[0], 200)
        self.assertEqual(self.rename()[0], 409)
        self.assertNotIn(b'MIAOSHOU_ACCOUNT_3_NAME=', self.path.read_bytes())
        self.assertNotEqual(before, self.path.read_bytes())

    def test_remove_backs_up_and_clears_only_target_history_without_vendor_calls(self):
        other = server.automation_pool(2)
        other.put_item(123, 'site', 0, {'title': 'other account'})
        other.start_job('a' * 32, 123, 7, 'US')
        other.update_job('a' * 32, 'save', 'ready_publish', 'other task')
        server.order_store(2).upsert([{'packageId': 7, 'orderNo': 'other order'}])
        pool = server.automation_pool(3)
        pool.put_item(123, 'site', 0, {'title': 'retained'})
        pool.start_job('a' * 32, 123, 7, 'US')
        pool.update_job('a' * 32, 'save', 'ready_publish', 'retained pending task')
        server.order_store(3).upsert([{'packageId': 7, 'orderNo': 'retained order'}])
        before = pool.get_job('a' * 32)
        backup_pool_name = pool.pools[before['poolId'] - 1].path.name
        for _ in range(2):
            status, body = self.remove()
            self.assertEqual(status, 200, body)
            self.assertNotIn(3, [a['id'] for a in body['data']['accounts']])
            self.assertEqual(body['data']['removedAccounts'], [])
        data = self.path.read_text()
        self.assertNotIn('test-secret-3', data)
        self.assertNotIn('MIAOSHOU_ACCOUNT_3_APP_KEY', data)
        self.assertIn('MIAOSHOU_ACCOUNT_3_REMOVED=1', data)
        self.assertIn('MIAOSHOU_APP_SECRET=secret-one', data)
        self.assertEqual(os.getenv('APP_MODE'), 'live')
        self.assertEqual(server.MiaoShouClient(2).app_key, 'test-two')
        self.assertFalse(server.MiaoShouClient(3).configured)
        self.assertIsNone(pool.get_job('a' * 32))
        self.assertEqual(server.order_store(3).summary()['count'], 0)
        self.assertEqual(pool.list_items(), [])
        backups = list((self.root / 'backups').glob('remove-account-3-*'))
        self.assertEqual(len(backups), 1)
        restored = server.AutomationPool(backups[0] / backup_pool_name)
        self.assertEqual(restored.get_job('a' * 32), {key: value for key, value in before.items() if key != 'poolId'})
        history = self.service.listing('queue', {})
        self.assertEqual([row['accountId'] for row in history['jobs']], [2])
        self.assertEqual(other.get_job('a' * 32)['message'], 'other task')
        self.assertEqual(server.order_store(2).summary()['count'], 1)
        with self.assertRaises(ValueError):
            self.service.check_account(3)
        self.assertEqual(self.calls, [])

    def test_old_removed_account_cleanup_removes_transfers_but_preserves_other_batch(self):
        with self.service.db() as connection:
            connection.execute("INSERT INTO shared_batches VALUES ('batch','{}','{}','submitted',0,8)")
            for key, source, target in [('deleted', 3, 1), ('retained', 2, 1)]:
                connection.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                    (key, 'batch', json.dumps({'sourceAccountId': source, 'targetAccountId': target}), 'needs_review', '', None, 0))
        account_credentials.remove_credentials(self.path, 3)
        os.environ['MIAOSHOU_ACCOUNT_3_REMOVED'] = '1'
        self.assertEqual(self.remove()[0], 200)
        with self.service.db() as connection:
            self.assertEqual([r[0] for r in connection.execute('SELECT transfer_id FROM shared_transfers')], ['retained'])
            self.assertEqual(connection.execute('SELECT count(*) FROM shared_batches').fetchone()[0], 1)
        self.assertEqual(self.request('GET', '/api/miaoshou-accounts/manage')[1]['data']['removedAccounts'], [])

    def test_backup_failure_does_not_retire_credentials_or_delete_history(self):
        import account_history
        with patch.object(account_history.Path, 'mkdir', side_effect=PermissionError('fixture')):
            self.assertEqual(self.remove()[0], 500)
        self.assertTrue(server.MiaoShouClient(3).configured)
        self.assertNotIn(3, server.ACCOUNT_SLOT_STORE.history_cleared_ids())

    def test_old_confirmation_cannot_clear_history(self):
        self.assertEqual(self.remove(body={'confirmation': 'REMOVE_COLLECTION_ACCOUNT'})[0], 409)
        self.assertTrue(server.MiaoShouClient(3).configured)

    def test_removed_id_never_reused_or_resurrected_by_add_empty_delete_or_inherited_environment(self):
        self.assertEqual(self.remove()[0], 200)
        status, body = self.request('POST', '/api/miaoshou-accounts', {'confirmation': 'ADD_EMPTY_COLLECTION'})
        self.assertEqual(status, 201)
        self.assertEqual(body['data']['id'], 4)
        self.assertNotIn(3, [a['id'] for a in body['data']['accounts']])
        status, body = self.request('DELETE', '/api/miaoshou-accounts/4', {'confirmation': 'DELETE_EMPTY_COLLECTION'})
        self.assertEqual(status, 200)
        self.assertNotIn(3, [a['id'] for a in body['data']['accounts']])
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_3_APP_KEY': 'inherited',
                                    'MIAOSHOU_ACCOUNT_3_APP_SECRET': 'inherited-secret', 'MIAOSHOU_ACCOUNT_3_REMOVED': '0'}):
            server.load_local_env()
            self.assertFalse(server.MiaoShouClient(3).configured)
            self.assertNotIn(3, [a['id'] for a in server.miaoshou_account_slots()])
        with self.assertRaises(account_credentials.CredentialConfigError):
            server.save_collection_credentials(3, 'new-account', 'new-secret')

    def test_removed_source_preserves_identity_but_disables_jobs_in_active_target(self):
        original_scope = self.service.check_account(3)['scope']
        target_scope = self.service.check_account(1)['scope']
        pool = server.automation_pool(1)
        pool.put_item(456, 'site', 0, {'title': 'retained target'})
        pool.start_job('b' * 32, 456, 7, 'US')
        pool.update_job('b' * 32, 'save', 'ready_publish', 'retained target')
        with self.service.db() as connection:
            connection.execute('INSERT INTO shared_job_origins VALUES (?,?,?,?,?,?)',
                               (1, 'b' * 32, 3, original_scope, 123, target_scope))
        self.assertEqual(self.remove()[0], 200)
        self.assertNotIn(3, [account['id'] for account in self.service.accounts()])
        with self.service.db() as connection:
            retired = connection.execute('SELECT scope FROM shared_accounts WHERE account_id=3').fetchone()
        self.assertEqual(retired['scope'], original_scope)
        row = self.service.job_rows()[0]
        self.assertEqual(row['sourceAccountId'], 3)
        self.assertFalse(row['available'])
        with self.assertRaises(ValueError):
            self.service.assert_job(1, 'b' * 32)

    def test_primary_unknown_origin_confirmation_and_injected_fields_are_rejected(self):
        before = self.path.read_bytes()
        for account in (1, 9, 20):
            self.assertEqual(self.remove(account)[0], 409)
        for headers in ({}, {'Origin': 'http://evil.example'}, {**self.headers, 'Host': 'evil.example'}):
            self.assertEqual(self.remove(headers=headers)[0], 403)
        for body in ({}, {'confirmation': 'REMOVE_COLLECTION_ACCOUNT', 'APP_MODE': 'demo'}):
            self.assertEqual(self.remove(body=body)[0], 409)
        self.assertEqual(self.path.read_bytes(), before)

    def test_queued_running_publish_and_shared_work_block_removal(self):
        pool = server.automation_pool(2)
        pool.put_item(123, 'site', 0, {'title': 'other account'})
        job = pool.queue_jobs([123], 7, 'US')[0]
        job_id = job['jobId']
        before = self.path.read_bytes()
        self.assertEqual(self.remove()[0], 409)
        pool.start_next_queued_jobs(1)
        self.assertEqual(self.remove()[0], 409)
        pool.update_job(job_id, 'save', 'ready_publish', 'test')
        batch = pool.begin_publish_batch([job_id], 1)
        self.assertEqual(self.remove()[0], 409)
        pool.finish_publish_batch(batch)
        with self.service.db() as connection:
            connection.execute("INSERT INTO shared_transfers VALUES ('t','b','{}','queued','',NULL,0)")
        self.assertEqual(self.remove()[0], 409)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(server.MiaoShouClient(3).configured)

    def test_active_submission_sync_or_vendor_call_blocks_removal(self):
        for name, value in (('ACCOUNT_MUTATIONS_ACTIVE', 1), ('ACCOUNT_VENDOR_ACTIVE', {3: 1}), ('DEEPSEEK_REQUESTS_ACTIVE', 1)):
            with patch.object(server, name, value):
                self.assertEqual(self.remove()[0], 409)
        for lock in (server.ORDER_SYNC_LOCKS[3], server.AUTOMATION_EDIT_SCHEDULER_LOCK, server._automation_publish_lock):
            lock.acquire()
            try:
                self.assertEqual(self.remove()[0], 409)
            finally:
                lock.release()

    def test_request_guard_tracks_submission_until_completed_and_stale_client_cannot_call(self):
        entered, release = Event(), Event()
        @server.guard_account_mutation
        def submit(_handler):
            entered.set(); release.wait(3)
        thread = Thread(target=submit, args=(SimpleNamespace(path='/api/item/save', collection_account_id=2),))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.remove()[0], 409)
        finally:
            release.set(); thread.join(3)
        self.assertEqual(server.ACCOUNT_MUTATIONS_ACTIVE, 0)
        client = server.MiaoShouClient(3)
        self.assertEqual(self.remove()[0], 200)
        with patch.object(server, 'build_opener') as remote:
            with self.assertRaisesRegex(RuntimeError, '已删除'):
                REAL_VENDOR_POST(client, server.HALF_CREATE, {})
            remote.assert_not_called()

    def test_file_failure_leaves_account_and_deepseek_active_and_cleans_temporary_secrets(self):
        self.assertEqual(self.deepseek()[0], 200)
        before = self.path.read_bytes()
        with patch.object(account_credentials.os, 'replace', side_effect=PermissionError('test-ai-key')):
            self.assertEqual(self.remove()[0], 500)
            status, body = self.deepseek(remove=True)
            self.assertEqual(status, 500)
            self.assertNotIn('test-ai-key', str(body))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(server.MiaoShouClient(3).configured)
        self.assertTrue(server.DeepSeekClient().configured)
        self.assertEqual(list(self.root.glob('.env-setup-*')), [])

    def test_parallel_removal_and_ai_save_do_not_clobber_each_other(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = [future.result() for future in [executor.submit(self.remove), executor.submit(self.remove), executor.submit(self.deepseek)]]
        self.assertEqual([row[0] for row in results], [200, 200, 200])
        text = self.path.read_text()
        self.assertIn('DEEPSEEK_API_KEY=test-ai-key', text)
        self.assertIn('MIAOSHOU_ACCOUNT_3_REMOVED=1', text)
        self.assertNotIn('MIAOSHOU_ACCOUNT_3_APP_SECRET', text)

    def test_deepseek_save_replace_remove_reload_is_local_and_preserves_model_mode_and_miaoshou(self):
        self.path.write_bytes(codecs.BOM_UTF8 + self.path.read_bytes().replace(b'\n', b'\r\n') + b'DEEPSEEK_MODEL=fixture-model\r\n')
        for key in ('test-ai-key', 'test-ai-key-2'):
            status, body = self.deepseek(key)
            self.assertEqual(status, 200, body)
            self.assertTrue(body['data']['configured'])
            self.assertNotIn(key, str(body))
            self.assertEqual(server.DeepSeekClient().api_key, key)
        self.assertEqual(self.deepseek(remove=True)[0], 200)
        self.assertFalse(server.DeepSeekClient().configured)
        data = self.path.read_bytes()
        self.assertNotIn(b'DEEPSEEK_API_KEY=', data)
        self.assertIn(b'DEEPSEEK_MODEL=fixture-model\r\n', data)
        self.assertTrue(data.startswith(codecs.BOM_UTF8))
        self.assertIn(b'MIAOSHOU_ACCOUNT_3_APP_SECRET=test-secret-3', data)
        self.assertEqual(os.getenv('APP_MODE'), 'live')
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'inherited-secret', 'DEEPSEEK_DISABLED': '0'}):
            server.load_local_env()
            self.assertFalse(server.DeepSeekClient().configured)
        self.assertEqual(self.calls, [])

    def test_deepseek_origin_validation_busy_guard_and_status_never_leak_secret(self):
        self.assertEqual(self.deepseek(headers={})[0], 403)
        for invalid in ('', 'secret\nAPP_MODE=demo', 'x' * 513, 123):
            self.assertEqual(self.deepseek(invalid)[0], 409)
        with patch.object(server, 'DEEPSEEK_REQUESTS_ACTIVE', 1):
            self.assertEqual(self.deepseek()[0], 409)
        self.assertEqual(self.deepseek()[0], 200)
        status, body = self.request('GET', '/api/settings/deepseek', headers={'X-Miaoshou-Account-Id': '19'})
        self.assertEqual(status, 200)
        self.assertTrue(body['data']['configured'])
        self.assertNotIn('test-ai-key', str(body))
        with patch.object(server, 'ACCOUNT_MUTATIONS_ACTIVE', 1):
            self.assertEqual(self.deepseek(remove=True)[0], 409)

    def test_stale_ai_client_is_blocked_without_a_network_call(self):
        self.assertEqual(self.deepseek()[0], 200)
        client = server.DeepSeekClient()
        self.assertEqual(self.deepseek('replacement')[0], 200)
        with patch.object(server, 'build_opener') as remote:
            with self.assertRaisesRegex(RuntimeError, '更新'):
                client._completion([])
            remote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
