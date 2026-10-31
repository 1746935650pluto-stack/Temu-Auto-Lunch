"""Status query regressions against disposable pools; no backend or remote I/O."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Barrier
import unittest
from unittest.mock import patch

import task_queries
from task_queries import PersonnelResolver, TaskQueries
from test_task_queries import QueryFixture


class QueryFilters181Tests(QueryFixture):
    def physical_job(self, account, number, detail, status='queued', shop=7, person='11', observe=True):
        pool = self.pools[account].pools[number - 1]
        pool.put_item(detail, 'site', 0, {'title': f'item {detail}', 'subAppAccountId': person})
        if observe:
            self.people.observe(f'scope-{account}', [{'collectBoxDetailId': detail, 'subAppAccountId': person}])
        job_id = pool.queue_jobs([detail], shop, 'US')[0]['jobId']
        with closing(pool._connect()) as db, db:
            db.execute('UPDATE automation_jobs SET status=?,created_at=? WHERE job_id=?',
                       (status, '2026-10-21T01:00:00+00:00', job_id))
        return job_id

    def test_status_mixed_pages_keep_tab_counts_and_hydrate_only_the_page(self):
        for index in range(35):
            self.physical_job(1, index % 3 + 1, 200 + index, 'needs_review')
        for index, status in enumerate(['failed'] * 11 + ['queued'] * 5 + ['ready_publish'] * 3 + ['done'] * 2):
            self.physical_job(1, index % 3 + 1, 400 + index, status)
        self.transfers(85)
        with self.service.db() as db:
            db.execute("UPDATE shared_transfers SET status='queued' WHERE transfer_id<'t0020'")
        original = TaskQueries.hydrate_jobs
        hydrated = []

        def hydrate(queries, rows):
            hydrated.append(len(rows))
            return original(queries, rows)

        seen = set()
        with patch.object(TaskQueries, '_tasks_full', side_effect=AssertionError('status used full path')), \
                patch.object(TaskQueries, 'hydrate_jobs', hydrate):
            for page in (1, 2):
                data = self.service.listing('jobs', {'status': ['needs_review'], 'taskGroup': ['attention'], 'page': [str(page)]})
                self.assertEqual((data['page'], data['pages'], data['total']), (page, 2, 100))
                self.assertEqual(len(data['entries']), 50)
                self.assertTrue(all(row['status'] == 'needs_review' for row in data['entries']))
                self.assertEqual(data['counts'], {'processing': 5, 'attention': 46, 'publish': 3, 'history': 2})
                self.assertEqual(data['transferCounts'], {'processing': 20, 'attention': 65, 'publish': 0, 'history': 0})
                self.assertEqual(data['totalAll'], 56)
                self.assertEqual(data['shopPendingCounts'], [{'accountId': 1, 'shopId': 7, 'count': 3}])
                keys = {row['key'] for row in data['entries']}
                self.assertFalse(keys & seen)
                seen |= keys
                self.assertEqual(len(data['jobs']) + len(data['transfers']), 50)
        self.assertEqual(hydrated, [0, 35])
        self.assertEqual(len(seen), 100)

    def test_conflicting_group_status_and_queue_are_empty_without_losing_counts(self):
        self.physical_job(1, 1, 201, 'queued')
        self.physical_job(1, 2, 202, 'needs_review')
        self.physical_job(2, 3, 203, 'ready_publish')
        deleted = self.physical_job(2, 3, 204, 'done')
        self.pools[2].pools[2].delete_job_records([deleted], allow_pending=True)
        self.transfers(2)
        with patch.object(TaskQueries, '_tasks_full', side_effect=AssertionError('status used full path')):
            for kind, group in (('jobs', 'processing'), ('queue', 'attention')):
                with self.subTest(kind=kind):
                    data = self.service.listing(kind, {'status': ['needs_review'], 'taskGroup': [group], 'page': ['2']})
                    self.assertEqual((data['total'], data['page'], data['pages'], data['entries']), (0, 1, 1, []))
                    self.assertEqual(data['counts'], {'processing': 1, 'attention': 1, 'publish': 1, 'history': 0})
                    self.assertEqual(data['transferCounts']['attention'], 2)
                    self.assertEqual(data['totalAll'], 3)
                    self.assertEqual(data['shopPendingCounts'], [{'accountId': 2, 'shopId': 7, 'count': 1}])

    def test_status_account_shop_pool_and_noncanonical_shop_match_full_path(self):
        self.physical_job(1, 1, 201, 'needs_review', shop=7)
        self.physical_job(2, 1, 202, 'needs_review', shop=7)
        self.physical_job(2, 1, 203, 'failed', shop=7)
        self.physical_job(2, 2, 204, 'needs_review', shop=7)
        self.physical_job(2, 1, 205, 'needs_review', shop=8)
        self.physical_job(1, 3, 206, 'ready_publish', shop=9)
        self.transfers(3)
        cases = ({'accountId': ['2'], 'shopId': ['7']},
                 {'accountId': ['2'], 'shopId': ['7'], 'poolId': ['1']},
                 {'accountId': ['2'], 'shopId': ['8']},
                 {'accountId': ['2'], 'shopId': ['007']},
                 {'accountId': ['99']}, {'poolId': ['99']})
        for filters in cases:
            query = {'status': ['needs_review'], **filters}
            expected = TaskQueries(self.service)._tasks_full(query, '')
            with self.subTest(filters=filters), patch.object(TaskQueries, '_tasks_full', side_effect=AssertionError('full path')):
                actual = self.service.listing('jobs', query)
                for key in ('total', 'counts', 'transferCounts', 'totalAll', 'shopPendingCounts', 'personOptions'):
                    self.assertEqual(actual[key], expected[key], key)
                self.assertEqual([r['key'] for r in actual['entries']], [r['key'] for r in expected['entries']])
        pool_only = self.service.listing('jobs', {'status': ['needs_review'], 'accountId': ['2'], 'poolId': ['1'], 'shopId': ['7']})
        self.assertEqual(pool_only['total'], 1)
        self.assertEqual(pool_only['transfers'], [])

    def test_status_selector_preserves_global_historical_and_fallback_owners(self):
        self.physical_job(1, 1, 201, 'queued', person='33', observe=False)
        historical = self.physical_job(2, 2, 202, 'needs_review')
        self.people.observe('old-scope', [{'collectBoxDetailId': 777, 'subAppAccountId': '99'}])
        self.people.update_names('old-scope', [{'subAppAccountId': '99', 'name': 'Historical owner'}])
        self.people.update_names('scope-1', [{'subAppAccountId': '44', 'name': 'Unused configured owner'}])
        with self.service.db() as db:
            db.execute('INSERT INTO shared_job_origins VALUES (?,?,?,?,?,?)', (2, historical, 1, 'old-scope', 777, 'scope-2'))
        query = {'status': ['needs_review'], 'accountId': ['2'], 'poolId': ['2']}
        expected = TaskQueries(self.service)._tasks_full(query, '')
        with patch.object(TaskQueries, '_tasks_full', side_effect=AssertionError('full path')):
            actual = self.service.listing('jobs', query)
        self.assertEqual(actual['personOptions'], expected['personOptions'])
        self.assertEqual({row['personKey'] for row in actual['personOptions']}, {'1:33', '1:99'})
        self.assertEqual(actual['jobs'][0]['personName'], 'Historical owner')

    def test_selector_cache_expiry_and_context_changes_never_cache_task_results(self):
        self.physical_job(1, 1, 201)
        self.people.update_names('scope-1', [{'subAppAccountId': '11', 'name': 'Owner'},
                                            {'subAppAccountId': '22', 'name': 'Future owner'}])
        original = TaskQueries._read_job_person_options
        with patch.object(TaskQueries, '_read_job_person_options', autospec=True, side_effect=original) as read:
            with patch('task_queries.time.monotonic', return_value=100):
                first = self.service.listing('jobs', {'status': ['queued']})
            first['personOptions'][0]['personName'] = 'mutated response'
            self.physical_job(1, 2, 202, person='22')
            with patch('task_queries.time.monotonic', return_value=114):
                warm = self.service.listing('jobs', {'status': ['queued']})
            self.assertEqual(read.call_count, 1)
            self.assertEqual(warm['total'], 2)
            self.assertEqual({row['personId'] for row in warm['jobs']}, {'11', '22'})
            self.assertEqual([row['personKey'] for row in warm['personOptions']], ['1:11'])
            self.assertEqual(warm['personOptions'][0]['personName'], 'Owner')
            with patch('task_queries.time.monotonic', return_value=115):
                expired = self.service.listing('jobs', {'status': ['queued']})
            self.assertEqual(read.call_count, 2)
            self.assertEqual({row['personKey'] for row in expired['personOptions']}, {'1:11', '1:22'})
            self.people.update_names('scope-1', [{'subAppAccountId': '11', 'name': 'Renamed'}])
            with patch('task_queries.time.monotonic', return_value=116):
                renamed = self.service.listing('jobs', {'status': ['queued']})
            self.assertEqual(read.call_count, 3)
            self.assertEqual(next(row for row in renamed['personOptions'] if row['personId'] == '11')['personName'], 'Renamed')
            self.accounts[0].update(scope='replacement-scope', blocked=True)
            with patch('task_queries.time.monotonic', return_value=117):
                replaced = self.service.listing('jobs', {'status': ['queued']})
            self.assertEqual(read.call_count, 4)
            self.assertEqual({row['personKey'] for row in replaced['personOptions']}, {'1:11', '1:22'})
            self.assertTrue(all(not row['available'] for row in replaced['jobs']))

    def test_selector_cold_requests_share_one_scan(self):
        self.physical_job(1, 1, 201)
        queries = [TaskQueries(self.service), TaskQueries(self.service)]
        resolvers = [PersonnelResolver(self.service, self.accounts), PersonnelResolver(self.service, self.accounts)]
        ready = Barrier(2)
        original = TaskQueries._read_job_person_options

        def read(index):
            ready.wait(timeout=5)
            return queries[index]._status_person_options(resolvers[index])

        with patch.object(TaskQueries, '_read_job_person_options', autospec=True, side_effect=original) as scan:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(read, (0, 1)))
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(results[0], results[1])

    def test_full_status_counts_include_legacy_states_without_a_second_count_scan(self):
        for index, status in enumerate(('queued', 'needs_review', 'ready_publish', 'legacy_pending')):
            self.physical_job(1, index % 3 + 1, 201 + index, status)
        self.transfers(2)
        statements = []
        original = task_queries.reader

        @contextmanager
        def traced_reader(*args, **kwargs):
            with original(*args, **kwargs) as db:
                db.set_trace_callback(statements.append)
                yield db

        with patch('task_queries.reader', traced_reader):
            all_rows = self.service.listing('jobs', {'personKey': ['1:11']})
            selected = self.service.listing('jobs', {'personKey': ['1:11'], 'status': ['legacy_pending']})
            conflict = self.service.listing('jobs', {'personKey': ['1:11'], 'status': ['legacy_pending'], 'taskGroup': ['attention']})
        self.assertEqual(all_rows['total'], 6)
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['entries'][0]['status'], 'legacy_pending')
        self.assertEqual(selected['counts'], {'processing': 1, 'attention': 1, 'publish': 1, 'history': 0})
        self.assertEqual(selected['transferCounts']['attention'], 2)
        self.assertEqual(conflict['total'], 0)
        self.assertFalse(any('SELECT COUNT(*) FROM base b' in statement for statement in statements))
        with patch.object(TaskQueries, '_tasks_full', side_effect=AssertionError('full path')):
            fast = self.service.listing('jobs', {'status': ['legacy_pending']})
            missing = self.service.listing('jobs', {'status': ['absent_status']})
        self.assertEqual(fast['total'], 1)
        self.assertEqual(missing['total'], 0)


if __name__ == '__main__':
    unittest.main()
