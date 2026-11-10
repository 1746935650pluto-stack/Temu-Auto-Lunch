"""Temporary local bindings and synthetic logistics, with no real vendor writes."""
import copy
import json
import os
import unittest
from unittest.mock import patch

import server
import test_shared_automation as fixtures


class ShippingBindingTests(unittest.TestCase):
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server

    def setUp(self):
        fixtures.SharedAutomationTests.setUp(self)
        self.headers = {'Origin': f'http://127.0.0.1:{self.httpd.server_port}'}
        original = server.MiaoShouClient.post
        def post(client, path, payload):
            result = original(client, path, payload)
            if path in (server.HALF_FREIGHT, server.HALF_WAREHOUSE):
                field, id_key, name_key = ('shopIdAndFreightTemplateListMap', 'templateId', 'templateName') if path == server.HALF_FREIGHT else ('shopIdAndWarehouseListMap', 'warehouseId', 'warehouseName')
                result['data'][field][str(payload['shopId'])].append({
                    'site': 'US', id_key: f'{client.account_id}-custom-{id_key}', name_key: '自选物流'})
            return result
        mocked = patch.object(server.MiaoShouClient, 'post', post)
        mocked.start(); self.addCleanup(mocked.stop)

    def body(self, account=2, **extra):
        data = server.shipping_settings(account, 7, 'US')
        return {'accountId': account, 'shopId': 7, 'site': 'US', 'scope': data['scope'],
                'revision': data['binding']['revision'], 'freightId': f'{account}-custom-templateId',
                'warehouseId': f'{account}-custom-warehouseId', 'confirmation': 'SAVE_SHIPPING_BINDING', **extra}

    def test_http_choices_are_account_scoped_and_save_is_local_only(self):
        status, response = self.request('GET', '/api/settings/shipping?accountId=2&shopId=7&site=US')
        self.assertEqual(status, 200, response)
        self.assertEqual(response['data']['freight'][-1]['id'], '2-custom-templateId')
        body = self.body()
        self.calls.clear()
        status, response = self.request('POST', '/api/settings/shipping', body, self.headers)
        self.assertEqual(status, 200, response)
        self.assertEqual(response['data']['binding']['freightId'], '2-custom-templateId')
        self.assertEqual(server.bound_logistics(1, 7, 'US')['freightId'], '')
        self.assertTrue(all(path in {server.SHOP_LIST, server.HALF_SITES, server.HALF_FREIGHT, server.HALF_WAREHOUSE} for _, path, _ in self.calls))
        status, response = self.request('GET', '/api/logistics?accountId=2&shopId=7')
        self.assertEqual(status, 200, response)
        self.assertEqual(response['data']['bindings']['US']['warehouseId'], '2-custom-warehouseId')

    def test_rejects_foreign_choices_invalid_scope_and_stale_save_without_changing_binding(self):
        body = self.body()
        self.assertEqual(self.request('POST', '/api/settings/shipping', body, self.headers)[0], 200)
        before = server.bound_logistics(2, 7, 'US')
        for change in ({'revision': 0}, {'scope': ''}, {'freightId': '1-custom-templateId', 'revision': 1},
                       {'shopId': 9}, {'site': 'EU'}, {'revision': True}):
            with self.subTest(change=change):
                self.assertEqual(self.request('POST', '/api/settings/shipping', {**body, **change}, self.headers)[0], 400)
                self.assertEqual(server.bound_logistics(2, 7, 'US'), before)
        self.assertEqual(self.request('POST', '/api/settings/shipping', body, {})[0], 403)

    def test_reset_is_versioned_persistent_and_credential_changes_do_not_reuse_binding(self):
        body = self.body()
        self.assertEqual(self.request('POST', '/api/settings/shipping', body, self.headers)[0], 200)
        store = server.shipping_bindings()
        self.assertEqual(store.get(body['scope'], 7, 'US')['revision'], 1)
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_SECRET': 'changed'}):
            self.assertEqual(server.bound_logistics(2, 7, 'US')['freightId'], '')
        reset = {**body, 'revision': 1, 'confirmation': 'RESET_SHIPPING_BINDING'}
        self.assertEqual(self.request('POST', '/api/settings/shipping', reset, self.headers)[0], 200)
        self.assertEqual(store.get(body['scope'], 7, 'US'), {'freightId': '', 'warehouseId': '', 'revision': 2})
        self.assertEqual(self.request('POST', '/api/settings/shipping', body, self.headers)[0], 400)

    def test_copy_uses_target_binding_and_stale_binding_never_falls_back(self):
        body = self.body()
        self.assertEqual(self.request('POST', '/api/settings/shipping', body, self.headers)[0], 200)
        plan = self.service.preview(fixtures.SharedAutomationTests.request_data(self))
        transfer = plan['transfers'][0]
        item = copy.deepcopy(self.remote[(1, 123)])
        item['collectBoxDetailShopList'] = [{'shopId': 7}]
        self.calls.clear()
        self.service._prepare_copy_logistics(item, transfer)
        row = item['collectBoxDetailShopList'][0]
        self.assertEqual(row['siteFreightTemplateList'][0]['freightTemplateId'], '2-custom-templateId')
        self.assertEqual(row['siteWarehouseList'][0]['warehouseId'], '2-custom-warehouseId')
        self.assertFalse(any(path == server.HALF_CREATE for _, path, _ in self.calls))
        server.shipping_bindings().save(body['scope'], 2, 7, 'US', 'missing', 'missing', 1)
        with self.assertRaisesRegex(ValueError, '已失效'):
            self.service._prepare_copy_logistics(item, transfer)

    def test_default_is_unchanged_and_manual_option_must_exist_in_current_rows(self):
        rows = [{'templateId': 'auto', 'templateName': '通用'}, {'templateId': 'custom', 'templateName': '自选'}]
        self.assertEqual(server.automation_bound_logistic_id(rows, 'freight', {}), 'auto')
        self.assertEqual(server.automation_bound_logistic_id(rows, 'freight', {'freightId': 'custom'}), 'custom')
        with self.assertRaises(ValueError):
            server.automation_bound_logistic_id(rows, 'freight', {'freightId': 'old'})
