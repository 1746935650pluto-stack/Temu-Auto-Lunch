"""Full account workflows. Synthetic credentials, temporary DBs, no real writes."""
import os
import unittest
from unittest.mock import patch

import server
import test_order_accounts as order_fixtures
from test_item_editor import sample_item


class CollectionAccountTests(unittest.TestCase):
    request = order_fixtures.OrderAccountTests.request
    stop_server = order_fixtures.OrderAccountTests.stop_server

    def setUp(self):
        order_fixtures.OrderAccountTests.setUp(self)
        runtime = patch.object(server.RUNTIME, 'lanes', {})
        runtime.start()
        self.addCleanup(runtime.stop)
        directory = server.ORDER_SYNC_STORE.path.parent
        for name, value in (
            ('AUTOMATION_POOL', server.AutomationPool(directory / 'pool.sqlite3')),
            ('ACCOUNT_POOLS', {}), ('ACCOUNT_CATEGORY_CACHES', {}), ('ACCOUNT_SUMMARIES', {}),
            ('COLLECTION_PEOPLE', server.CollectionPeople(server.PersonnelStore(directory / 'people.sqlite3'))),
        ):
            mocked = patch.object(server, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.calls = []
        self.item = sample_item()

        def post(client, path, payload):
            self.calls.append((client.app_key, path, payload))
            data = {'shopList': [{'shopId': 2, 'shopNick': client.app_key}],
                    'shopIdAndFreightTemplateListMap': {str(payload.get('shopId', 2)): [
                        {'site': 'US', 'templateName': '通用', 'templateId': 'freight-2'}]},
                    'shopIdAndWarehouseListMap': {str(payload.get('shopId', 2)): [
                        {'site': 'US', 'warehouseName': '发货', 'warehouseId': 'warehouse-2'}]},
                    'siteCollectItemInfo': self.item, 'shopCollectItemInfo': self.item,
                    'detailList': [{'collectBoxDetailId': 123, 'title': client.app_key, 'subAppAccountId': '11'}],
                    'orderPackageList': [{'opOrderPackageId': 55, 'orderInfo': {'platformOrderSn': client.app_key}}],
                    'cateTree': {'label': client.app_key}}
            return {'result': 'success', 'code': 'success', 'data': data}

        for mocked in (patch.object(server.MiaoShouClient, 'post', post), patch.object(server.time, 'sleep')):
            mocked.start()
            self.addCleanup(mocked.stop)

    def request_account(self, method, path, body=None, account=2):
        return self.request(method, path, body, {'X-Miaoshou-Account-Id': str(account)})

    def test_read_workflow_uses_selected_credentials_including_editor(self):
        for path in ('/api/shops', '/api/sites', '/api/products', '/api/records',
                     '/api/item?detailId=123&mode=site', '/api/logistics?shopId=2', '/api/categories'):
            self.assertEqual(self.request_account('GET', path)[0], 200, path)
        self.assertTrue(self.calls)
        self.assertTrue(all(key == 'test-two' for key, _, _ in self.calls))
        self.assertEqual(self.request_account('GET', '/api/health')[1]['accountId'], 2)
        self.assertTrue(self.request_account('GET', '/api/health')[1]['multiAccountWorkbench'])

    def test_manual_editor_save_remains_account_scoped_and_read_only_mode_blocks_writes(self):
        payload = {'detailId': 123, 'mode': 'site', 'item': self.item,
                   'version': server.fingerprint(self.item), 'confirmation': 'SAVE_MIAOSHOU_ITEM'}
        status, result = self.request_account('POST', '/api/item/save', payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.calls[-1][:2], ('test-two', server.HALF_SITE_SAVE))
        self.calls.clear()
        with patch.dict(os.environ, {'APP_MODE': 'demo'}):
            self.assertEqual(self.request_account('POST', '/api/item/save', payload)[0], 409)
            self.assertEqual(self.request('POST', '/api/orders/sync', {'accountId': 2})[0], 200)
        self.assertTrue(all(path == server.MIAOSHOU_PACKAGE_LIST_PATH for _, path, _ in self.calls))
        self.assertEqual(server.ORDER_SYNC_STORE.summary()['count'], 0)
        self.assertEqual(server.order_store(2).summary()['count'], 1)

    def test_identical_item_and_job_ids_cannot_cross_account_pools(self):
        for account in (1, 2):
            pool = server.automation_pool(account)
            pool.put_item(123, 'site', 0, {'title': f'account-{account}'})
            pool.start_job('a' * 32, 123, 2, 'US')
        self.assertEqual(self.request_account('GET', '/api/automation/pool')[1]['data']['items'][0]['title'], 'account-2')
        self.assertEqual(len(self.request_account('GET', '/api/automation/jobs')[1]['data']['jobs']), 1)
        # Local deletion/removal must not target account 1, even with identical IDs.
        server.automation_pool(2).update_job('a' * 32, 'prepare', 'failed', 'fixture')
        status, body = self.request_account('DELETE', '/api/automation/jobs/' + 'a' * 32)
        self.assertEqual(status, 200, body)
        self.assertIsNotNone(server.automation_pool(1).get_job('a' * 32))
        self.assertIsNone(server.automation_pool(2).get_job('a' * 32))
        self.assertEqual(self.calls, [])

    def test_background_batch_captures_account_and_only_enqueues_its_pool(self):
        server.automation_pool(2).put_item(123, 'site', 0, {'title': 'two'})
        with patch.object(server, 'Thread') as worker:
            status, body = self.request_account('POST', '/api/automation/batch-run', {
                'detailIds': [123], 'shopIds': [2], 'site': 'US', 'confirmation': 'AUTOMATE_AND_SAVE_BATCH'})
            self.assertEqual(status, 202, body)
            args = worker.call_args.kwargs['args']
            self.assertIs(args[0], server.automation_pool(2))
            args[1](server.SHOP_LIST, {})
            self.assertEqual(self.calls[-1][0], 'test-two')
        self.assertEqual(server.AUTOMATION_POOL.list_jobs(), [])
        self.assertEqual(len(server.automation_pool(2).list_jobs()), 1)
        with patch.object(server, 'Thread') as worker:
            server.resume_queued_automation_jobs_after_start(server.automation_pool(2), 2)
            worker.call_args.kwargs['args'][1](server.SHOP_LIST, {})
            self.assertEqual(self.calls[-1][0], 'test-two')

    def test_mismatch_invalid_and_unconfigured_never_fall_back(self):
        for account in ('0', '99', '../1', ''):
            self.assertEqual(self.request_account('GET', '/api/shops', account=account)[0], 400)
        self.assertEqual(self.request_account('GET', '/api/products?accountId=1')[0], 400)
        self.assertEqual(self.request_account('POST', '/api/item/create', {'accountId': 1})[0], 400)
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': ''}):
            self.assertEqual(self.request_account('GET', '/api/shops')[0], 400)
        self.assertEqual(self.calls, [])

    def test_categories_and_page_counts_are_not_shared(self):
        two = self.request_account('GET', '/api/categories')[1]['data']
        with patch.object(server, '_category_cache', None):
            one = self.request_account('GET', '/api/categories', account=1)[1]['data']
        self.assertNotEqual(one, two)
        server._measure_collect_box_pagination_in_background(lambda *_: {'data': {'detailList': []}}, 2)
        self.assertEqual(server.ACCOUNT_SUMMARIES[2]['totalItems'], 0)
        self.assertEqual(server.collect_box_pagination_summary(None, 2)['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
