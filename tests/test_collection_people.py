"""Personnel identity, filtering and read-only routing; no real vendor calls."""
import http.client
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch, Mock
from urllib.parse import urlencode

import server
from collection_people import (PERSONNEL_CACHE_TTL_SECONDS, CollectionPeople,
                               PersonnelStore, collector_id)


class CollectionPeopleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = PersonnelStore(Path(directory.name) / 'people.sqlite3')
        self.people = CollectionPeople(self.store, page_size=2)
        self.assertEqual(self.people.ttl, PERSONNEL_CACHE_TTL_SECONDS)
        self.assertEqual(PERSONNEL_CACHE_TTL_SECONDS, 7 * 24 * 60 * 60)

    def test_identity_never_falls_back_to_parent_account_or_name(self):
        rows = [{'collectBoxDetailId': 1, 'appAccountId': 99, 'name': 'A'},
                {'collectBoxDetailId': 2, 'subAppAccountId': 11},
                {'collectBoxDetailId': 3, 'subAppAccountId': '11'},
                {'collectBoxDetailId': 4, 'subAppAccountId': '9007199254740993'}]
        self.store.observe('one', rows)
        self.store.update_names('one', [{'subAppAccountId': 11, 'name': '同名'},
                                        {'subAppAccountId': '9007199254740993', 'name': '同名'}])
        people = self.store.list('one')
        self.assertEqual([p['id'] for p in people], ['11', '9007199254740993'])
        self.assertEqual([p['name'] for p in people], ['同名', '同名'])
        self.assertEqual(collector_id(True), '')
        self.assertEqual(collector_id(1.5), '')
        self.assertEqual(collector_id('00011'), '11')
        self.assertEqual(self.store.list('two'), [])

    def test_names_persist_rename_and_missing_or_conflicting_name_clears_mapping(self):
        self.store.update_names('one', [{'subAppAccountId': 11, 'name': '旧名字'}])
        reopened = PersonnelStore(self.store.path)
        self.assertEqual(reopened.list('one')[0]['name'], '旧名字')
        reopened.update_names('one', [{'subAppAccountId': 11, 'name': '新名字', 'userName': 'login'}])
        self.assertEqual(reopened.list('one')[0]['name'], '新名字')
        reopened.update_names('one', [{'subAppAccountId': 11}])
        self.assertEqual(reopened.list('one')[0]['name'], '')
        reopened.update_names('one', [{'subAppAccountId': 11, 'name': 'A'}, {'subAppAccountId': 11, 'name': 'B'}])
        self.assertEqual(reopened.list('one')[0]['name'], '')

    def test_optional_names_failure_cached_and_manual_refresh_retries(self):
        self.store.observe('one', [{'subAppAccountId': 11}])
        self.assertEqual(self.people.refresh_names('one'), 'unconfigured')
        reader = Mock(side_effect=RuntimeError('no permission'))
        self.assertEqual(self.people.refresh_names('one', reader), 'unavailable')
        self.assertEqual(self.people.refresh_names('one', reader), 'unavailable')
        self.assertEqual(reader.call_count, 1)
        reader.side_effect = None
        reader.return_value = {'data': {'memberList': [{'subAppAccountId': 11, 'name': 'Alice'}]}}
        self.assertEqual(self.people.refresh_names('one', reader, force=True), 'ready')
        self.assertEqual(self.store.list('one')[0]['name'], 'Alice')
        reader.return_value = {'data': [{'id': 22, 'name': 'Not a sub-account mapping'}]}
        self.assertEqual(self.people.refresh_names('two', reader), 'unmatched')
        self.assertEqual(self.store.list('two'), [])

    def test_aliases_persist_update_clear_and_survive_remote_name_refresh(self):
        self.store.observe('one', [{'subAppAccountId': 11}, {'subAppAccountId': 22}])
        self.store.observe('two', [{'subAppAccountId': 11}])
        self.store.save_aliases('one', [{'id': '11', 'alias': '  小王  '}, {'id': '22', 'alias': '小王'}])
        reopened = PersonnelStore(self.store.path)
        self.assertEqual([p['alias'] for p in reopened.list('one')], ['小王', '小王'])
        self.assertEqual(reopened.list('two')[0]['alias'], '')
        reopened.update_names('one', [{'subAppAccountId': 11, 'name': '妙手名称', 'userName': 'login'}])
        self.assertEqual(reopened.list('one')[0]['alias'], '小王')
        reopened.save_aliases('one', [{'id': '11', 'alias': '新备注'}, {'id': '22', 'alias': '  '}])
        self.assertEqual([p['alias'] for p in reopened.list('one')], ['新备注', ''])
        reopened.save_aliases('one', [{'id': '11', 'alias': ''}])
        self.assertEqual(reopened.list('one')[0]['name'], '妙手名称')
        self.assertEqual(reopened.list('one')[0]['alias'], '')

    def test_alias_validation_and_database_failure_are_atomic(self):
        self.store.observe('one', [{'subAppAccountId': 11}, {'subAppAccountId': 22}])
        self.store.save_aliases('one', [{'id': '11', 'alias': '原备注'}])
        for aliases in (None, [], {}, [None], [{'id': True, 'alias': 'x'}],
                        [{'id': '11', 'alias': 12}], [{'id': '11', 'alias': 'x' * 81}],
                        [{'id': '11', 'alias': 'a\nb'}],
                        [{'id': '11', 'alias': 'new'}, {'id': '0011', 'alias': 'duplicate'}],
                        [{'id': '11', 'alias': 'new'}, {'id': '99', 'alias': 'unknown'}]):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                self.store.save_aliases('one', aliases)
            self.assertEqual(self.store.list('one')[0]['alias'], '原备注')
        with closing(self.store._connect()) as connection, connection:
            connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON personnel_aliases "
                               "WHEN NEW.id='22' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.Error):
            self.store.save_aliases('one', [{'id': '11', 'alias': 'new'}, {'id': '22', 'alias': 'fail'}])
        self.assertEqual([p['alias'] for p in self.store.list('one')], ['原备注', ''])

    def test_global_scan_paginates_matches_and_keeps_accounts_separate(self):
        rows = [{'collectBoxDetailId': i, 'subAppAccountId': 11 if i % 2 else 22} for i in range(1, 8)]
        reader = Mock(side_effect=lambda page, size: {'data': {'detailList': rows[(page-1)*size:page*size]}})
        self.assertEqual(self.people.scan('one', reader)['status'], 'loading')
        self.assertEqual(self.people.filtered('one', '11', 1, 2), ([], 0))
        for _ in range(3):
            scan = self.people.scan('one', reader)
        self.assertEqual(scan, {'status': 'ready', 'scanned': 7, 'message': ''})
        page, total = self.people.filtered('one', '11', 2, 2)
        self.assertEqual([row['collectBoxDetailId'] for row in page], [5, 7])
        self.assertEqual(total, 4)
        self.assertEqual(self.people.filtered('one', '11', 1, 2, '2'), ([], 0))
        self.assertEqual(self.people.filtered('two', '11', 1, 2), ([], 0))
        self.people.scan('one', reader)
        self.assertEqual(reader.call_count, 4)
        self.people.scan('one', reader, refresh=True)
        self.assertEqual(reader.call_args.args, (1, 2))

    def test_failed_or_repeating_scan_never_exposes_partial_results(self):
        reader = Mock(return_value={'data': {'detailList': [
            {'collectBoxDetailId': 1, 'subAppAccountId': 11},
            {'collectBoxDetailId': 2, 'subAppAccountId': 11}]}})
        self.people.scan('one', reader)
        self.assertEqual(self.people.scan('one', reader)['status'], 'failed')
        self.assertEqual(self.people.filtered('one', '11', 1, 20), ([], 0))
        reader.side_effect = RuntimeError('offline')
        self.assertEqual(self.people.scan('two', reader)['status'], 'failed')

    def test_title_search_matches_full_box_and_combines_with_person_and_id(self):
        rows = [
            {'collectBoxDetailId': 1, 'subAppAccountId': 11, 'title': '冬季保暖手套 Purple / S'},
            {'collectBoxDetailId': 2, 'subAppAccountId': 22, 'title': '夏季帽子'},
            {'collectBoxDetailId': 3, 'subAppAccountId': 22, 'title': '紫色手套 PURPLE'},
            {'collectBoxDetailId': 4, 'subAppAccountId': 11, 'title': 'C++ [100%]'},
            {'collectBoxDetailId': 5, 'title': None},
        ]
        reader = Mock(side_effect=lambda page, size: {'data': {'detailList': rows[(page-1)*size:page*size]}})
        self.people.scan('one', reader)
        self.assertEqual(self.people.filtered('one', '', 1, 1, title_keyword='手套'), ([], 0))
        self.people.scan('one', reader); self.people.scan('one', reader)
        matches, total = self.people.filtered('one', '', 2, 1, title_keyword='手套')
        self.assertEqual(total, 2)
        self.assertEqual(matches[0]['collectBoxDetailId'], 3)
        self.assertEqual(self.people.filtered('one', '', 1, 20, title_keyword='  purple  ')[1], 2)
        matches, total = self.people.filtered('one', '11', 1, 20, title_keyword='手套')
        self.assertEqual([row['collectBoxDetailId'] for row in matches], [1])
        self.assertEqual(total, 1)
        self.assertEqual(self.people.filtered('one', '22', 1, 20, '1', '手套'), ([], 0))
        self.assertEqual(self.people.filtered('one', '', 1, 20, '3', '手套')[1], 1)
        self.assertEqual(self.people.filtered('one', '', 1, 20, title_keyword='C++ [100%]')[1], 1)
        self.assertEqual(self.people.filtered('one', '', 1, 20, title_keyword='.*'), ([], 0))
        self.assertEqual(self.people.filtered('two', '', 1, 20, title_keyword='手套'), ([], 0))
        self.assertEqual(self.people.filtered('one', '', 1, 20, title_keyword='不存在'), ([], 0))

    def test_concurrent_scan_reads_each_page_once(self):
        started, finish = threading.Event(), threading.Event()
        def reader(page, size):
            started.set()
            if not finish.wait(2):
                raise RuntimeError('test timed out')
            return {'data': {'detailList': [{'collectBoxDetailId': 1, 'subAppAccountId': 11}]}}
        worker = threading.Thread(target=lambda: self.people.scan('one', reader))
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            other = Mock()
            self.assertEqual(self.people.scan('one', other)['status'], 'loading')
            other.assert_not_called()
        finally:
            finish.set()
            worker.join(3)
        self.assertEqual(self.people.filtered('one', '11', 1, 20)[1], 1)


class CollectionPeopleRouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, value in (('COLLECTION_PEOPLE', CollectionPeople(PersonnelStore(root / 'people.sqlite3'), page_size=10)),
                            ('ACCOUNT_SLOT_STORE', server.AccountSlotStore(root / 'slots.sqlite3'))):
            patcher = patch.object(server, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'MIAOSHOU_APP_KEY': 'one', 'MIAOSHOU_APP_SECRET': 'secret1',
            'MIAOSHOU_ACCOUNT_2_APP_KEY': 'two', 'MIAOSHOU_ACCOUNT_2_APP_SECRET': 'secret2'}, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.calls = []
        self.remote = patch.object(server.AppHandler, '_account_vendor', side_effect=self.vendor)
        self.remote.start(); self.addCleanup(self.remote.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.AppHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(2)

    def vendor(self, path, payload, account_id):
        self.calls.append((path, payload, account_id))
        self.assertEqual(path, server.HALF_BOX_LIST)
        rows = [{'collectBoxDetailId': i, 'subAppAccountId': '9007199254740993' if i % 2 else '22',
                 'appAccountId': 999, 'title': f'account-{account_id} item-{i}'} for i in range(1, 48)]
        start = (payload['pageNo'] - 1) * payload['pageSize']
        return {'data': {'detailList': rows[start:start + payload['pageSize']]}}

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def post_aliases(self, body, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        try:
            connection.request('POST', '/api/collection-personnel/aliases', json.dumps(body),
                               headers={'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_alias_post_is_local_scoped_and_read_interfaces_include_alias(self):
        for account in (1, 2):
            self.get(f'/api/products?accountId={account}')
        self.calls.clear()
        body = {'accountId': 1, 'aliases': [{'id': '22', 'alias': '张三'}]}
        status, result = self.post_aliases(body)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['accountId'], 1)
        self.assertEqual(next(p['alias'] for p in result['data']['people'] if p['id'] == '22'), '张三')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.get('/api/collection-personnel?refresh=1')[1]['data']['people'][0]['alias'], '张三')
        self.assertEqual(self.get('/api/collection-personnel?accountId=2')[1]['data']['people'][0]['alias'], '')
        with patch.dict(os.environ, {'MIAOSHOU_APP_KEY': 'changed'}):
            self.assertEqual(self.get('/api/collection-personnel')[1]['data']['people'], [])
            self.assertEqual(self.post_aliases(body)[0], 400)
        self.assertEqual(self.calls, [])
        products = self.get('/api/products')[1]['data']
        self.assertEqual(next(p['alias'] for p in products['people'] if p['id'] == '22'), '张三')

    def test_alias_post_rejects_invalid_account_unknown_ids_origin_and_handles_storage_failure(self):
        self.get('/api/products')
        self.calls.clear()
        valid = {'accountId': 1, 'aliases': [{'id': '22', 'alias': 'name'}]}
        for body in ({**valid, 'accountId': account} for account in (None, True, 0, 99, 1.5)):
            self.assertEqual(self.post_aliases(body)[0], 400)
        for body in ({'aliases': valid['aliases']}, {**valid, 'aliases': [{'id': '999', 'alias': 'name'}]}, [], None):
            self.assertEqual(self.post_aliases(body)[0], 400)
        self.assertEqual(self.post_aliases(valid, {'Origin': 'https://example.com'})[0], 403)
        with patch.object(server.COLLECTION_PEOPLE.store, 'save_aliases', side_effect=sqlite3.OperationalError('locked')):
            status, body = self.post_aliases(valid)
            self.assertEqual(status, 500)
            self.assertIn('本机人员备注保存失败', body['error'])
        self.assertEqual(self.calls, [])

    def test_list_preserves_exact_person_ids_and_filter_spans_remote_pages(self):
        status, body = self.get('/api/products?page=1')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['data']['detailList'][0]['subAppAccountId'], '9007199254740993')
        self.assertEqual(body['data']['accountId'], 1)
        for _ in range(5):
            status, body = self.get('/api/products?collectorId=9007199254740993&page=2')
        self.assertEqual(status, 200)
        result = body['data']
        self.assertEqual(result['filter']['status'], 'ready')
        self.assertEqual(result['filter']['totalItems'], 24)
        self.assertEqual([row['collectBoxDetailId'] for row in result['data']['detailList']], [41, 43, 45, 47])
        self.assertEqual(self.get('/api/products?collectorId=22&collectBoxDetailId=1')[1]['data']['data']['detailList'], [])
        self.assertTrue(all(call[0] == server.HALF_BOX_LIST for call in self.calls))

    def test_accounts_credentials_and_missing_permissions_are_isolated(self):
        self.get('/api/products?accountId=1')
        self.get('/api/products?accountId=2')
        self.assertEqual([call[2] for call in self.calls], [1, 2])
        status, body = self.get('/api/collection-personnel?accountId=2')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['mappingStatus'], 'unconfigured')
        self.assertEqual(len(body['data']['people']), 2)
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': 'different'}):
            self.assertEqual(self.get('/api/collection-personnel?accountId=2')[1]['data']['people'], [])
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': ''}):
            self.assertEqual(self.get('/api/products?accountId=2')[0], 400)
        self.assertEqual(len(self.calls), 2)

    def test_title_query_full_pagination_combined_filters_empty_and_account_isolation(self):
        path = '/api/products?' + urlencode({'titleKeyword': '  ACCOUNT-1 ITEM-  ', 'page': 2})
        status, body = self.get(path)
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['filter']['status'], 'loading')
        self.assertEqual(body['data']['data']['detailList'], [])
        for _ in range(4):
            status, body = self.get(path)
        self.assertEqual(body['data']['filter']['totalItems'], 47)
        self.assertEqual(body['data']['filter']['totalPages'], 3)
        self.assertEqual([r['collectBoxDetailId'] for r in body['data']['data']['detailList']], list(range(21, 41)))
        combined = self.get('/api/products?titleKeyword=item-1&collectorId=22')[1]['data']
        self.assertEqual([r['collectBoxDetailId'] for r in combined['data']['detailList']], [10, 12, 14, 16, 18])
        empty = self.get('/api/products?titleKeyword=missing')[1]['data']
        self.assertEqual(empty['filter']['totalItems'], 0)
        self.assertEqual(empty['data']['detailList'], [])
        exact = self.get('/api/products?titleKeyword=item-1&collectBoxDetailId=1')[1]['data']
        self.assertEqual([r['collectBoxDetailId'] for r in exact['data']['detailList']], [1])
        self.assertEqual(len(self.calls), 5)  # All follow-up searches reused the complete scan.
        other = self.get('/api/products?accountId=2&titleKeyword=account-1')[1]['data']
        self.assertEqual(other['filter']['status'], 'loading')
        self.assertEqual(other['data']['detailList'], [])
        for _ in range(4):
            other = self.get('/api/products?accountId=2&titleKeyword=account-1')[1]['data']
        self.assertEqual(other['filter']['totalItems'], 0)
        self.assertTrue(all(call[0] == server.HALF_BOX_LIST for call in self.calls))
        unfiltered = self.get('/api/products?titleKeyword=%20%20')[1]['data']
        self.assertNotIn('filter', unfiltered)
        self.assertEqual(len(unfiltered['data']['detailList']), 20)

    def test_invalid_title_rejected_before_vendor_and_failed_scan_is_not_empty_success(self):
        for title in ('x' * 201, 'bad\x00name'):
            self.assertEqual(self.get('/api/products?' + urlencode({'titleKeyword': title}))[0], 400)
        self.assertEqual(self.calls, [])
        with patch.object(server.AppHandler, '_account_vendor', side_effect=RuntimeError('offline')):
            status, body = self.get('/api/products?titleKeyword=hat')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['filter']['status'], 'failed')
        self.assertEqual(body['data']['data']['detailList'], [])

    def test_configured_member_read_is_cached_and_write_paths_are_rejected(self):
        path = '/open/v1/test/member/get_member_list'
        with patch.dict(os.environ, {'MIAOSHOU_PERSONNEL_LIST_PATH': path}), \
             patch.object(server.AppHandler, '_account_vendor', return_value={'data': {'memberList': [
                 {'subAppAccountId': 11, 'name': '姓名', 'userName': '账号', 'password': 'must-not-store'}]}}) as remote:
            for _ in range(2):
                status, body = self.get('/api/collection-personnel')
                self.assertEqual(status, 200)
                self.assertEqual(body['data']['people'][0]['name'], '姓名')
            remote.assert_called_once_with(path, {}, 1)
            self.assertNotIn('password', json.dumps(body))
            self.assertEqual(self.get('/api/collection-personnel?accountId=2')[1]['data']['people'], [])
        with patch.dict(os.environ, {'MIAOSHOU_PERSONNEL_LIST_PATH': '/open/v1/member/save_member'}), \
             patch.object(server.AppHandler, '_account_vendor') as remote:
            self.assertEqual(self.get('/api/collection-personnel')[1]['data']['mappingStatus'], 'unavailable')
            remote.assert_not_called()

    def test_bad_queries_and_origin_rejected_before_vendor_read(self):
        for path in ('/api/products?collectorId=true', '/api/products?accountId=99',
                     '/api/products?collectBoxDetailId=bad', '/api/products?accountId=0'):
            self.assertEqual(self.get(path)[0], 400)
        self.assertEqual(self.get('/api/collection-personnel', {'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
