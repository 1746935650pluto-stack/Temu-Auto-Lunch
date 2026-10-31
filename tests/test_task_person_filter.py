"""Source ownership filtering uses isolated SQLite and mocked vendor calls."""
import json
import os
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import server
import test_shared_automation as fixtures
from collection_people import PersonnelStore, CollectionPeople
from shared_automation import SharedAutomation


class TaskPersonFilterTests(unittest.TestCase):
    setUp = fixtures.SharedAutomationTests.setUp
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server
    request_data = fixtures.SharedAutomationTests.request_data
    prepare = fixtures.SharedAutomationTests.prepare

    def observe(self, account, detail, person, name='同名人员'):
        store = server.COLLECTION_PEOPLE.store
        scope = self.service.scope(account)
        store.observe(scope, [{'collectBoxDetailId': detail, 'subAppAccountId': person}])
        store.update_names(scope, [{'subAppAccountId': person, 'name': name}])

    def queue(self, account=1, detail=123, shops=(7,), status='ready_publish'):
        pool = server.automation_pool(account)
        jobs = pool.queue_jobs([detail], list(shops), 'US')
        with closing(pool._connect()) as db, db:
            for job in jobs:
                db.execute('UPDATE automation_jobs SET status=? WHERE job_id=?', (status, job['jobId']))
        return jobs

    def test_account_qualified_person_filters_all_pages_and_keeps_global_shop_totals(self):
        for account in (1, 2):
            self.observe(account, 123, '11')
        self.queue(1, shops=tuple(range(7, 68)))
        self.queue(2)
        self.calls.clear()
        for endpoint in ('jobs', 'queue'):
            data = self.service.listing(endpoint, {'personKey': ['1:11'], 'page': ['2']})
            self.assertEqual((data['total'], data['pages'], len(data['jobs'])), (61, 2, 11))
            self.assertEqual(data['counts']['publish'], 61)
            self.assertTrue(all(r['sourceAccountId'] == 1 and r['personKey'] == '1:11' for r in data['jobs']))
            self.assertEqual({r['personKey'] for r in data['personOptions']}, {'1:11', '2:11'})
            self.assertEqual(sum(r['count'] for r in data['shopPendingCounts']), 62)
        other = self.service.listing('queue', {'personKey': ['2:11'], 'keyword': ['absent']})
        self.assertEqual(other['total'], 0)
        self.assertEqual(sum(r['count'] for r in other['shopPendingCounts']), 62)
        self.assertEqual(self.calls, [])

    def test_cross_account_jobs_and_pending_transfers_use_source_not_target(self):
        self.observe(2, 123, '22', '来源人员')
        self.observe(1, 123, '11', '目标人员')
        _, transfers = self.prepare((2,), ((1, 7),))
        data = self.service.listing('jobs', {'personKey': ['2:22'], 'taskGroup': ['processing']})
        self.assertEqual(data['transferCounts']['processing'], 1)
        self.assertEqual(data['transfers'][0]['personName'], '来源人员')
        self.assertEqual(self.service.listing('jobs', {'personKey': ['1:11']})['transfers'], [])
        self.service.prepare_transfer(transfers[0])
        job = self.service.job_rows()[0]
        self.observe(1, job['detailId'], '99', '副本人员')
        self.calls.clear()
        saved = self.service.listing('jobs', {'personKey': ['2:22']})
        self.assertEqual(saved['total'], 1)
        self.assertEqual(saved['jobs'][0]['personName'], '来源人员')
        self.assertEqual(saved['jobs'][0]['accountId'], 1)
        self.assertEqual(self.calls, [])

    def test_unknown_persons_and_transfer_filter_precede_limit_and_counts(self):
        item = {'title': '无归属'}
        server.automation_pool(1).put_item(456, 'site', 0, item)
        self.queue(detail=456, status='failed')
        _, transfers = self.prepare((2,), ((1, 7),))
        raw = dict(transfers[0]); payload = json.loads(raw['payload'])
        with self.service.db() as db:
            for i in range(60):
                payload['detailId'] = 1000 + i; payload['personId'] = '22' if i == 0 else '33'
                db.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                    (f'transfer-{i}', raw['batch_id'], json.dumps(payload), 'needs_review', '', None, i))
        filtered = self.service.listing('jobs', {'personKey': ['2:22'], 'taskGroup': ['attention']})
        self.assertEqual(filtered['transferCounts']['attention'], 1)
        self.assertEqual(filtered['transfers'][0]['transfer_id'], 'transfer-0')
        unknown = self.service.listing('jobs', {'personKey': ['unknown']})
        self.assertEqual(unknown['total'], 1)
        self.assertEqual(unknown['jobs'][0]['personName'], '未知归属')
        self.assertEqual(unknown['counts']['attention'], 1)

    def test_names_aliases_restart_removed_items_and_credentials_remain_isolated(self):
        self.observe(1, 123, '11')
        store = server.COLLECTION_PEOPLE.store
        store.save_aliases(self.service.scope(1), [{'id': '11', 'alias': '本机备注'}])
        self.queue(status='done')
        self.service.job_rows()  # capture identity before removing the source
        server.automation_pool(1).remove_items([123])
        server.COLLECTION_PEOPLE = CollectionPeople(PersonnelStore(store.path))
        self.service = SharedAutomation(server, self.service.path)
        row = self.service.listing('jobs', {'personKey': ['1:11'], 'taskGroup': ['history']})['jobs'][0]
        self.assertEqual(row['personName'], '本机备注')
        with patch.dict(os.environ, {'MIAOSHOU_APP_KEY': 'changed-key'}):
            changed_scope = self.service.scope(1)
            self.observe(1, 123, '99', '另一个凭据的人员')
            data = self.service.listing('jobs', {'personKey': ['1:11']})
            self.assertEqual(data['total'], 1)
            self.assertEqual(data['jobs'][0]['personName'], '本机备注')
            self.assertFalse(data['jobs'][0]['available'])
            self.assertNotEqual(changed_scope, self.service.check_account(2)['scope'])

    def test_local_legacy_cache_backfill_and_missing_fields_do_not_erase_evidence(self):
        path = server.COLLECTION_PEOPLE.store.path.parent / 'legacy.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE collection_cached_rows(scope TEXT,detail_id TEXT,payload TEXT)')
            db.executemany('INSERT INTO collection_cached_rows VALUES (?,?,?)', [
                ('old', '123', json.dumps({'subAppAccountId': '11'})),
                ('other', '123', json.dumps({'subAppAccountId': '22'})),
                ('old', '456', json.dumps({'name': '不能猜测', 'appAccountId': '11'}))])
        store = PersonnelStore(path)
        self.assertEqual(store.owners('old'), {'123': '11'})
        self.assertEqual(store.owners('other'), {'123': '22'})
        store.observe('old', [{'collectBoxDetailId': 123}, {'collectBoxDetailId': 123, 'subAppAccountId': None}])
        self.assertEqual(store.owners('old'), {'123': '11'})
        store.observe('old', [{'collectBoxDetailId': 123, 'subAppAccountId': '33'}])
        self.assertEqual(PersonnelStore(path).owners('old', [('123', '11')]), {'123': '33'})
        # Persisted display evidence never authorizes personnel routing.
        self.assertEqual(CollectionPeople(store).ownership('old')[0]['status'], 'unavailable')

    def test_quantity_and_multi_shop_preview_respect_person_and_do_not_publish(self):
        self.observe(1, 123, '11')
        self.observe(2, 123, '11')
        self.queue(1, shops=(7, 8))
        self.queue(2, shops=(7, 8))
        self.calls.clear()
        for extras in ({}, {'shopCounts': [{'accountId': 1, 'shopId': 7, 'publishCount': 2},
                                          {'accountId': 2, 'shopId': 7, 'publishCount': 2}]}):
            plan = self.service.publish_preview({'publishCount': 4, 'filters': {'personKey': '1:11'}, **extras})
            self.assertTrue(plan['count'] > 0)
            self.assertTrue(all(r['personKey'] == '1:11' for r in plan['sample']))
            with self.service.db() as db:
                payload = json.loads(db.execute('SELECT payload FROM shared_publish_previews WHERE preview_id=?',
                                               (plan['previewId'],)).fetchone()[0])
            self.assertTrue(all(r['accountId'] == 1 for r in payload['jobs']))
        self.assertEqual(self.calls, [])

    def test_http_filters_and_invalid_person_keys_are_not_ignored(self):
        self.observe(1, 123, '11'); self.queue()
        status, result = self.request('GET', '/api/automation/shared/queue?personKey=1%3A11')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['data']['jobs'][0]['personKey'], '1:11')
        for key in ('11', '1:0', '1:abc', '1:11:22', '<script>'):
            with self.assertRaises(ValueError):
                self.service.listing('jobs', {'personKey': [key]})
            with self.assertRaises(ValueError):
                self.service.publish_preview({'publishCount': 1, 'filters': {'personKey': key}})


if __name__ == '__main__':
    unittest.main()
