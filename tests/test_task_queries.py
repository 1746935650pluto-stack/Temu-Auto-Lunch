"""Read-path regressions; isolated fixtures, no server import or remote calls."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch

from automation_pool import AutomationPool
from collection_people import PersonnelStore, collector_id
from partitioned_pool import PartitionedPool
from shared_automation import SharedAutomation
from task_queries import TaskQueries, job_failure_metadata, reader


class QueryFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pools = {a: PartitionedPool(*(AutomationPool(self.root / f'{a}-{p}.sqlite3')
            for p in (1, 2, 3)), a) for a in (1, 2)}
        self.people = PersonnelStore(self.root / 'people.sqlite3')
        self.accounts = [{'id': a, 'name': f'Account {a}', 'scope': f'scope-{a}',
                          'configured': True, 'blocked': False} for a in (1, 2)]
        self.backend = SimpleNamespace(AUTOMATION_POOL=self.pools[1], automation_pool=self.pools.__getitem__,
            COLLECTION_PEOPLE=SimpleNamespace(store=self.people), collector_id=collector_id,
            AUTOMATION_EDIT_SCHEDULER_LOCK=Lock(), _automation_publish_lock=Lock(), ORDER_SYNC_LOCKS={})
        self.service = SharedAutomation(self.backend, self.root / 'shared.sqlite3')
        self.addCleanup(self.service.close)
        self.service.accounts = lambda: self.accounts
        for account in self.accounts:
            self.people.update_names(account['scope'], [{'id': '11', 'name': 'Owner'}])
        with self.service.db() as db:
            db.executemany('INSERT INTO shared_accounts VALUES (?,?)', [(a['id'], a['scope']) for a in self.accounts])

    def item(self, account, detail, person='11'):
        self.pools[account].put_item(detail, 'site', 0, {'title': f'item {detail}', 'subAppAccountId': person})
        self.people.observe(f'scope-{account}', [{'collectBoxDetailId': detail, 'subAppAccountId': person}])

    def job(self, account, detail, status='queued', created='2026-10-21T01:00:00+00:00'):
        self.item(account, detail)
        job = self.pools[account].queue_jobs([detail], 7, 'US')[0]
        pool = self.pools[account]._job_pool(job['jobId'])
        with closing(pool._connect()) as db, db:
            db.execute('UPDATE automation_jobs SET status=?,created_at=? WHERE job_id=?', (status, created, job['jobId']))
        return job['jobId']

    def transfers(self, count, status='needs_review', created=1792548000):
        with self.service.db() as db:
            db.execute('INSERT INTO shared_batches VALUES (?,?,?,?,?,?)', ('batch', '{}', '{}', 'submitted', created, 8))
            for i in range(count):
                payload = {'sourceAccountId': 1, 'sourceScope': 'scope-1', 'detailId': 1000 + i,
                           'targetAccountId': 2, 'targetScope': 'scope-2', 'shopIds': [7], 'title': f'copy {i}', 'personId': '11'}
                db.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                           (f't{i:04}', 'batch', json.dumps(payload), status, '', None, created + i))


class TaskQueriesTests(QueryFixture):
    def cache_collection_times(self, rows):
        with closing(self.people._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS collection_cached_rows ('
                       'scope TEXT, detail_id TEXT, payload TEXT, PRIMARY KEY(scope,detail_id))')
            db.executemany('INSERT OR REPLACE INTO collection_cached_rows VALUES (?,?,?)', rows)

    def test_pool_collection_time_recovers_from_exact_account_list_cache(self):
        self.item(1, 123)
        self.item(2, 123)
        self.cache_collection_times([
            ('scope-1', '123', json.dumps({'gmtCreate': '2026-10-22 17:39:35'})),
            ('scope-2', '123', json.dumps({'gmtCreate': '2026-10-20 12:00:00'})),
            ('retired-scope', '123', json.dumps({'gmtCreate': '2000-01-01 00:00:00'})),
            ('scope-1', '999', 'invalid unrelated payload'),
        ])
        result = TaskQueries(self.service).pool({})
        times = {row['key']: row['collectedAt'] for row in result['items']}
        self.assertEqual(times['1:123'], '2026-10-22 17:39:35')
        self.assertEqual(times['2:123'], '2026-10-20 12:00:00')

    def test_pool_collection_time_keeps_existing_snapshot_timestamp(self):
        self.item(1, 123)
        self.pools[1].put_item(123, 'site', 0, {'title': 'item', 'gmtCreate': '2026-10-21 08:00:00'})
        self.cache_collection_times([
            ('scope-1', '123', json.dumps({'gmtCreate': '2026-10-22 17:39:35'})),
        ])
        row = TaskQueries(self.service).pool({})['items'][0]
        self.assertEqual(row['collectedAt'], '2026-10-21 08:00:00')

    def test_pool_collection_time_does_not_use_other_scopes_or_local_entry_time(self):
        self.item(1, 123)
        self.cache_collection_times([
            ('scope-2', '123', json.dumps({'gmtCreate': '2026-10-22 17:39:35'})),
        ])
        row = TaskQueries(self.service).pool({})['items'][0]
        self.assertEqual(row['collectedAt'], '')
        self.assertTrue(row['createdAt'])

    def test_pool_collection_time_handles_missing_cache_and_invalid_payload(self):
        self.item(1, 123)
        self.assertEqual(TaskQueries(self.service).pool({})['items'][0]['collectedAt'], '')
        for payload in ['broken JSON', '{}', '{"gmtCreate":""}']:
            with self.subTest(payload=payload):
                self.cache_collection_times([('scope-1', '123', payload)])
                self.assertEqual(TaskQueries(self.service).pool({})['items'][0]['collectedAt'], '')

    def test_explicit_vendor_attribute_rejection_is_safe_to_retry(self):
        info = job_failure_metadata({'status': 'needs_review', 'step': 'save', 'available': True,
            'message': '重点展示部件，必选1个！；远端写入结果未确认，未自动重试，请到妙手核查'})
        self.assertEqual(info['failureCategory'], 'attributes')
        self.assertEqual(info['safeBatchAction'], 'retry')
        self.assertTrue(info['safeBatchEligible'])

    def test_transfer_only_queue_has_real_second_and_third_pages(self):
        self.transfers(125)
        seen = set()
        for page, count in ((1, 50), (2, 50), (3, 25)):
            data = self.service.listing('jobs', {'page': [str(page)], 'taskGroup': ['attention']})
            self.assertEqual((data['page'], data['pages'], data['total']), (page, 3, 125))
            self.assertEqual(len(data['entries']), count)
            self.assertEqual(data['jobs'], [])
            self.assertEqual(data['transferCounts']['attention'], 125)
            keys = {r['key'] for r in data['entries']}
            self.assertFalse(keys & seen)
            seen |= keys
        self.assertEqual(len(seen), 125)

    def test_jobs_and_transfers_share_order_and_page_capacity(self):
        for i in range(35):
            self.job(1, 200 + i, 'needs_review')
        self.transfers(85)
        keys = set()
        for page in (1, 2, 3):
            data = self.service.listing('jobs', {'page': [str(page)], 'taskGroup': ['attention']})
            self.assertEqual(data['total'], 120)
            self.assertEqual(len(data['entries']), 50 if page < 3 else 20)
            self.assertEqual(len(data['jobs']) + len(data['transfers']), len(data['entries']))
            self.assertFalse(keys & {r['key'] for r in data['entries']})
            keys |= {r['key'] for r in data['entries']}
        self.assertEqual(len(keys), 120)

    def test_task_page_hydrates_only_selected_jobs_and_never_event_history(self):
        for i in range(65):
            self.job(1, 200 + i)
        original = TaskQueries.hydrate_jobs
        hydrated = []
        def track(query, rows):
            hydrated.extend(rows)
            return original(query, rows)
        with patch.object(TaskQueries, 'hydrate_jobs', track), patch.object(AutomationPool, 'get_job', side_effect=AssertionError('detail hydration')):
            data = self.service.listing('jobs', {'page': ['2']})
        self.assertEqual(data['total'], 65)
        self.assertEqual(len(hydrated), 15)
        self.assertTrue(all(not any(k.startswith('_') for k in row) for row in data['entries']))

    def test_pool_pagination_filtering_owner_and_identical_ids_are_account_qualified(self):
        for account in (1, 2):
            for detail in range(101, 161):
                self.item(account, detail)
        self.people.observe('scope-2', [{'collectBoxDetailId': 120, 'subAppAccountId': '22'}])
        first = self.service.listing('pool', {})
        second = self.service.listing('pool', {'page': ['2']})
        self.assertEqual(first['total'], 120)
        self.assertEqual(len(first['items']), 50)
        self.assertFalse({r['key'] for r in first['items']} & {r['key'] for r in second['items']})
        owned = self.service.listing('pool', {'sourceAccountId': ['2'], 'personId': ['22']})
        self.assertEqual([r['key'] for r in owned['items']], ['2:120'])
        found = self.service.listing('pool', {'keyword': ['120']})
        self.assertEqual({r['key'] for r in found['items']}, {'1:120', '2:120'})

    def test_person_filter_uses_source_scope_not_target_copy_or_replaced_credentials(self):
        self.item(1, 123, '11')
        job = self.job(2, 456)
        self.people.observe('scope-2', [{'collectBoxDetailId': 456, 'subAppAccountId': '22'}])
        with self.service.db() as db:
            db.execute('INSERT INTO shared_job_origins VALUES (?,?,?,?,?,?)', (2, job, 1, 'scope-1', 123, 'scope-2'))
        data = self.service.listing('jobs', {'personKey': ['1:11']})
        self.assertEqual([r['jobId'] for r in data['jobs']], [job])
        self.assertEqual(self.service.listing('jobs', {'personKey': ['2:22']})['total'], 0)
        with self.service.db() as db:
            db.execute('UPDATE shared_job_origins SET source_scope=? WHERE job_id=?', ('old-scope', job))
        self.assertEqual(self.service.listing('jobs', {'personKey': ['unknown']})['total'], 1)

    def test_pool_clamps_oversized_page_before_sql_limit_and_after_removal(self):
        for detail in range(101, 152):
            self.item(1, detail)
        huge = {'page': ['9' * 50]}
        data = self.service.listing('pool', huge)
        self.assertEqual((data['page'], data['pages'], data['total'], len(data['items'])), (2, 2, 51, 1))
        self.pools[1].remove_items([101, 102])
        data = self.service.listing('pool', {'page': ['2']})
        self.assertEqual((data['page'], data['pages'], data['total'], len(data['items'])), (1, 1, 49, 49))
        data = self.service.listing('pool', {**huge, 'keyword': ['missing']})
        self.assertEqual((data['page'], data['total'], data['items']), (1, 0, []))

    def test_explicit_source_removal_preserves_legacy_history_owner_but_get_is_read_only(self):
        pool = self.pools[1]
        pool.put_item(123, 'site', 0, {'title': 'legacy', 'subAppAccountId': '11'})
        job = pool.queue_jobs([123], 7, 'US')[0]
        pool.update_job(job['jobId'], 'complete', 'done', 'fixture')
        self.assertEqual(self.service.listing('jobs', {'personKey': ['1:11']})['total'], 1)
        with reader(self.people.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM collection_product_owners').fetchone()[0], 0)
        result = self.service.remove_items([{'sourceAccountId': 1, 'detailId': 123}])
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(result['errors'])
        self.assertEqual(self.service.listing('jobs', {'personKey': ['1:11']})['total'], 1)
        self.assertEqual(self.service.listing('jobs', {'personKey': ['unknown']})['total'], 0)

    def test_owner_preservation_uses_retained_scope_and_never_overwrites_known_owner(self):
        self.pools[1].put_item(123, 'site', 0, {'title': 'legacy', 'subAppAccountId': '11'})
        self.accounts[0].update(scope='replacement-scope', blocked=True)
        self.people.observe('replacement-scope', [{'collectBoxDetailId': 123, 'subAppAccountId': '22'}])
        self.assertEqual(self.service.preserve_item_owners(1, [123]), 1)
        self.pools[1].put_item(123, 'site', 0, {'title': 'changed snapshot', 'subAppAccountId': '99'})
        self.assertEqual(self.service.preserve_item_owners(1, [123]), 0)
        with reader(self.people.path) as db:
            owners = dict(db.execute('SELECT scope,person_id FROM collection_product_owners WHERE detail_id=?', ('123',)))
        self.assertEqual(owners, {'scope-1': '11', 'replacement-scope': '22'})

    def test_daily_stats_cached_for_fifteen_seconds_and_not_shared_mutably(self):
        with patch.object(self.service, '_read_today_store_stats', return_value={'date': 'fixture', 'stores': []}) as read:
            with patch('shared_automation.time.monotonic', return_value=100):
                first = self.service.today_store_stats()
            first['stores'].append('not cached')
            with patch('shared_automation.time.monotonic', return_value=114):
                self.assertEqual(self.service.today_store_stats()['stores'], [])
            self.assertEqual(read.call_count, 1)
            with patch('shared_automation.time.monotonic', return_value=116):
                self.service.today_store_stats()
            self.assertEqual(read.call_count, 2)

    def test_legacy_job_keeps_original_owner_after_credential_replacement(self):
        job = self.job(1, 123)
        self.accounts[0].update(scope='replacement-scope', blocked=True)
        self.people.observe('replacement-scope', [{'collectBoxDetailId': 123, 'subAppAccountId': '22'}])
        page = self.service.listing('jobs', {'personKey': ['1:11']})
        self.assertEqual([r['jobId'] for r in page['jobs']], [job])
        self.assertFalse(page['jobs'][0]['available'])
        self.assertEqual(self.service.listing('jobs', {'personKey': ['1:22']})['total'], 0)

    def test_activity_never_waits_for_cold_or_expired_daily_report(self):
        entered, release = Event(), Event()
        def slow(now):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test did not release report')
            return {'date': now.date().isoformat(), 'timezone': 'Asia/Shanghai', 'stores': []}
        with patch.object(self.service, '_read_today_store_stats', slow):
            try:
                initial = self.service.activity()
                self.assertTrue(entered.wait(2))
                start = time.perf_counter()
                again = self.service.activity()
                self.assertLess(time.perf_counter() - start, .5)
                self.assertIsNone(initial['todayStoreStats']['generatedAt'])
                self.assertTrue(again['todayStoreStats']['refreshing'])
            finally:
                release.set()
                self.service.daily_stats_thread.join(5)
        self.assertIsNotNone(self.service.activity_store_stats()['generatedAt'])

    def test_event_queries_use_indexes_and_publish_does_not_consume_edit_slot(self):
        first = self.job(1, 123)
        second = self.job(1, 126)
        pool = self.pools[1]._job_pool(first)
        pool.update_job(first, 'publish', 'running', 'publishing')
        self.assertEqual([r['jobId'] for r in pool.start_next_queued_jobs(1)], [second])
        with reader(pool.path) as db:
            events = db.execute('EXPLAIN QUERY PLAN SELECT * FROM automation_job_events WHERE job_id=? ORDER BY event_id', (first,)).fetchall()
            day = db.execute('EXPLAIN QUERY PLAN SELECT * FROM automation_job_events WHERE created_at>=? AND created_at<?', ('a', 'z')).fetchall()
        self.assertTrue(any('automation_job_events_job_idx' in r['detail'] for r in events))
        self.assertTrue(any('automation_job_events_day_idx' in r['detail'] for r in day))

    def test_close_waits_for_daily_reader_and_prevents_new_refreshes(self):
        entered = Event()
        def stopping(now):
            entered.set()
            self.assertTrue(self.service.daily_stats_closed.wait(2))
            raise RuntimeError('shutdown')
        with patch.object(self.service, '_read_today_store_stats', side_effect=stopping) as read:
            self.service.activity_store_stats()
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.service.close())
            self.assertFalse(self.service.daily_stats_thread.is_alive())
            self.assertFalse(self.service.activity_store_stats()['refreshing'])
            self.service.today_store_stats()
            self.assertEqual(read.call_count, 1)


if __name__ == '__main__':
    unittest.main()
