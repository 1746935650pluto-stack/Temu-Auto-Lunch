from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import server
import test_order_accounts
from test_item_editor import sample_item
from test_attribute_defaults import rule


class DefaultRouteTests(unittest.TestCase):
    setUp = test_order_accounts.OrderAccountTests.setUp
    request = test_order_accounts.OrderAccountTests.request
    stop_server = test_order_accounts.OrderAccountTests.stop_server

    def test_editor_preview_and_manual_create_save_use_same_backend_defaults(self):
        item = sample_item()
        item['attributes'] = []
        rules = [rule()]
        calls = []
        def post(_client, path, payload):
            calls.append((path, deepcopy(payload)))
            if path == server.HALF_CATEGORY_ATTRIBUTE_RULES:
                return {'data': {'productAttributeRules': rules}, 'result': 'success', 'code': 'success'}
            return {'data': {}, 'result': 'success', 'code': 'success'}
        status, preview = self.request('POST', '/api/item/defaults', {'item': item, 'rules': rules})
        self.assertEqual(status, 200, preview)
        self.assertEqual(calls, [])
        with patch.object(server.MiaoShouClient, 'post', post), patch.object(server.AppHandler, '_get_item', return_value=item):
            status, body = self.request('POST', '/api/item/create', {'item': item, 'confirmation': 'CREATE_MIAOSHOU_ITEM'})
            self.assertEqual(status, 200, body)
            status, body = self.request('POST', '/api/item/save', {'item': item, 'mode': 'site', 'detailId': 123,
                'shopId': 0, 'version': server.fingerprint(item), 'confirmation': 'SAVE_MIAOSHOU_ITEM'})
            self.assertEqual(status, 200, body)
        writes = [payload['siteCollectItemInfo'] for path, payload in calls if path in (server.HALF_CREATE, server.HALF_SITE_SAVE)]
        self.assertEqual(len(writes), 2)
        for saved in writes:
            self.assertEqual(saved['attributes'], preview['data']['attributes'])
        ordinary = deepcopy(item)
        infer = Mock(side_effect=AssertionError('known defaults must not call AI'))
        server.complete_automation_attributes(ordinary, item, rules, 'category', infer, lambda _: None)
        self.assertEqual(ordinary['attributes'], preview['data']['attributes'])
        infer.assert_not_called()

    def test_health_distinguishes_runtime_and_new_resources(self):
        with patch.object(server, 'APP_VERSION', '1.0.0'):
            status, body = self.request('GET', '/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['runtimeVersion'], '1.0.0')
        self.assertNotEqual(body['resourceVersion'], body['runtimeVersion'])
        self.assertTrue(body['pendingUpdate'])
        self.assertTrue(body['capabilities']['unifiedTaskEntries'])


if __name__ == '__main__':
    unittest.main()
