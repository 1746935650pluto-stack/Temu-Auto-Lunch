"""Local personnel bindings and per-product routing. All vendor reads are mocked."""
import os
import unittest
from unittest.mock import Mock, patch

import server
import test_automation_routes as fixtures
from collection_people import PersonnelStore
from personnel_routing import personnel_targets


class PersonnelRoutingTests(unittest.TestCase):
    _stop_server = fixtures.AutomationRouteTests._stop_server
    request = fixtures.AutomationRouteTests.request

    def setUp(self):
        fixtures.AutomationRouteTests.setUp(self)
        self.rows = [{'collectBoxDetailId': 123, 'subAppAccountId': '11'},
                     {'collectBoxDetailId': 124, 'subAppAccountId': '22'},
                     {'collectBoxDetailId': 125, 'subAppAccountId': '33'},
                     {'collectBoxDetailId': 126, 'appAccountId': '11'}]
        for row in self.rows:
            server.AUTOMATION_POOL.put_item(row['collectBoxDetailId'], 'site', 0, {'title': '商品'})
        self.vendor = Mock(side_effect=self.read_vendor)
        for mocked in [patch.object(server, 'PERSONNEL_SHOP_CACHE', {}),
                       patch.object(server.AppHandler, '_collection_context', return_value=(1, 'primary', self.vendor)),
                       patch.dict(os.environ, {'APP_MODE': 'live'})]:
            mocked.start()
            self.addCleanup(mocked.stop)
        server.COLLECTION_PEOPLE.store.observe('primary', self.rows)

    def read_vendor(self, path, payload):
        if path == server.SHOP_LIST:
            return {'data': {'shopList': [{'shopId': 456, 'shopNick': '店铺一'}, {'shopId': 789, 'shopNick': '店铺二'}]}}
        if path == server.HALF_BOX_LIST:
            return {'data': {'detailList': self.rows}}
        raise AssertionError('Unexpected vendor call: ' + path)

    def binding(self, person='11', shops=None, **changes):
        return self.request('POST', '/api/automation/personnel-bindings',
                            {'accountId': 1, 'scope': 'primary', 'personId': person,
                             'shopIds': [456, 789] if shops is None else shops, **changes})

    def ready(self):
        status, body = self.request('GET', '/api/automation/personnel-routing?scan=1')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['scan']['status'], 'ready')
        return body['data']

    def payload(self):
        _, owners = server.COLLECTION_PEOPLE.ownership('primary')
        expected, _, _ = personnel_targets([123, 124, 125, 126], owners,
            server.COLLECTION_PEOPLE.store.bindings('primary'), {456, 789})
        return {'detailIds': [123, 124, 125, 126], 'targetMode': 'personnel', 'accountId': 1,
                'bindingScope': 'primary', 'expectedRouting': expected, 'site': 'US',
                'confirmation': 'AUTOMATE_AND_SAVE_BATCH'}

    def test_bindings_persist_clear_and_isolate_same_names_and_credentials(self):
        store = server.COLLECTION_PEOPLE.store
        store.update_names('primary', [{'subAppAccountId': '11', 'name': '同名'}, {'subAppAccountId': '22', 'name': '同名'}])
        store.save_aliases('primary', [{'id': '11', 'alias': '备注甲'}])
        self.assertEqual(self.binding()[0], 200)
        self.assertEqual(self.binding('22', [789])[0], 200)
        store.observe('other-credentials', [{'subAppAccountId': '11'}])
        store.save_binding('other-credentials', '11', [789], {789})
        reopened = PersonnelStore(store.path)
        self.assertEqual(reopened.bindings('primary'), {'11': [456, 789], '22': [789]})
        store.update_names('primary', [{'subAppAccountId': '11', 'name': '改名'}])
        self.assertEqual(reopened.bindings('primary')['11'], [456, 789])
        self.assertEqual(self.binding(shops=[])[0], 200)
        self.assertEqual(reopened.bindings('primary'), {'22': [789]})
        self.assertEqual(reopened.bindings('other-credentials'), {'11': [789]})
        self.assertEqual(self.ready()['people'][0]['alias'], '备注甲')
        self.assertEqual(server.AUTOMATION_POOL.list_jobs(), [])
        self.assertTrue(all(call.args[0] in {server.SHOP_LIST, server.HALF_BOX_LIST} for call in self.vendor.call_args_list))

    def test_invalid_bindings_origin_account_or_unknown_ids_do_not_mutate(self):
        self.binding()
        for changes in ({'scope': 'old'}, {'accountId': 2}, {'personId': '999'},
                        {'shopIds': [111]}, {'shopIds': [True]}, {'shopIds': '456'}, {'personId': True}):
            self.assertEqual(self.binding(**changes)[0], 400, changes)
        self.assertEqual(server.COLLECTION_PEOPLE.store.bindings('primary'), {'11': [456, 789]})
        self.assertEqual(self.request('POST', '/api/automation/personnel-bindings', {}, {'Origin': 'https://other.test'})[0], 403)
        self.assertEqual(self.request('GET', '/api/automation/personnel-routing?accountId=2')[0], 400)
        self.assertEqual(self.request('GET', '/api/automation/personnel-routing', headers={'Origin': 'https://other.test'})[0], 403)

    def test_scan_requires_complete_fresh_ownership_and_does_not_guess_parent(self):
        people = server.COLLECTION_PEOPLE
        people.page_size = 4
        result = self.request('GET', '/api/automation/personnel-routing?scan=1')[1]['data']
        self.assertEqual(result['scan']['status'], 'loading')
        self.assertEqual(result['owners'], {})
        self.rows = []
        result = self.ready()
        self.assertEqual(result['owners']['123'], '11')
        self.assertEqual(result['owners']['126'], '')
        self.assertEqual(people.ownership('other-account')[1], {})
        people.ttl = -1
        self.assertEqual(people.ownership('primary')[0]['status'], 'expired')
        self.assertEqual(people.ownership('primary')[1], {})

    def test_bound_batch_only_targets_selected_persons_shops_and_preserves_manual_publish(self):
        self.ready()
        self.binding('11', [456, 789])
        self.binding('22', [789])
        with patch.object(server, 'Thread') as worker:
            status, body = self.request('POST', '/api/automation/batch-run', self.payload())
            self.assertEqual(status, 202, body)
            data = body['data']
            self.assertEqual({(job['detailId'], job['shopId']) for job in data['jobs']}, {(123, 456), (123, 789), (124, 789)})
            self.assertEqual([row['detailId'] for row in data['skipped']], [125, 126])
            self.assertTrue(all(not job['publishAfterSave'] for job in data['jobs']))
            self.assertTrue(any('账号 ID 11' in event['message'] for event in data['jobs'][0]['events']))
            worker.return_value.start.assert_called_once()
            # A later binding change must not rewrite an already queued job's targets.
            self.binding('11', [789])
            self.assertEqual(len(server.AUTOMATION_POOL.list_jobs()), 3)

    def test_stale_or_tampered_routes_never_create_partial_jobs(self):
        self.ready()
        self.binding()
        payload = self.payload()
        with patch.object(server, 'Thread') as worker:
            for change in ({'bindingScope': 'other'}, {'expectedRouting': []}, {'accountId': 2},
                           {'targetMode': 'typo'}, {'detailIds': [True]}):
                self.assertEqual(self.request('POST', '/api/automation/batch-run', {**payload, **change})[0], 400)
            self.binding(shops=[789])
            self.assertEqual(self.request('POST', '/api/automation/batch-run', payload)[0], 400)
            payload = self.payload()
            server.COLLECTION_PEOPLE.ttl = -1
            self.assertEqual(self.request('POST', '/api/automation/batch-run', payload)[0], 400)
            worker.assert_not_called()
        self.assertEqual(server.AUTOMATION_POOL.list_jobs(), [])

    def test_no_binding_skips_all_without_worker_and_invalid_shop_does_not_fall_back(self):
        self.ready()
        with patch.object(server, 'Thread') as worker:
            status, body = self.request('POST', '/api/automation/batch-run', self.payload())
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['jobs'], [])
            self.assertEqual(len(body['data']['skipped']), 4)
            worker.assert_not_called()
        with self.assertRaises(ValueError):
            personnel_targets([123], {'123': '11'}, {'11': [999]}, {456})

    def test_queue_per_item_targets_validate_atomically(self):
        pool = server.AUTOMATION_POOL
        with self.assertRaises(ValueError):
            pool.queue_jobs([123, 124], [456], 'US', shops_by_detail={123: [456]})
        with self.assertRaises(ValueError):
            pool.queue_jobs([123, 999], [456], 'US', shops_by_detail={123: [456], 999: [456]})
        self.assertEqual(pool.list_jobs(), [])


if __name__ == '__main__':
    unittest.main()
