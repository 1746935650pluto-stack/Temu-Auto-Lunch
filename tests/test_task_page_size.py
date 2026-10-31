"""Task pagination never changes mutation limits or remote data."""
import unittest
from contextlib import closing
from shared_automation import SharedAutomation
from test_task_queries import QueryFixture


class TaskPageSizeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'key': f'1:{i:04}', 'createdAt': '2026-10-19'} for i in range(1001)]

    def test_allowed_sizes_have_no_gaps_or_duplicates_and_clamp_last_page(self):
        for size in (50, 100, 200, 500):
            with self.subTest(size=size):
                result = SharedAutomation.page(self.rows, {'pageSize': [str(size)]}, 'jobs')
                self.assertEqual(result['pageSize'], size)
                self.assertEqual(len(result['jobs']), size)
                collected = []
                for page in range(1, result['pages'] + 1):
                    chunk = SharedAutomation.page(self.rows, {'pageSize': [str(size)], 'page': [str(page)]}, 'jobs')
                    collected.extend(row['key'] for row in chunk['jobs'])
                self.assertEqual(len(set(collected)), 1001)
                self.assertEqual(len(collected), 1001)
                end = SharedAutomation.page(self.rows, {'pageSize': [str(size)], 'page': ['9999']}, 'jobs')
                self.assertEqual(end['page'], result['pages'])
                self.assertEqual(len(end['jobs']), 1)

    def test_default_pool_and_empty_results(self):
        self.assertEqual(SharedAutomation.page(self.rows, {}, 'jobs')['pageSize'], 50)
        self.assertEqual(len(SharedAutomation.page(self.rows, {'pageSize': ['500']})['items']), 500)
        empty = SharedAutomation.page([], {'pageSize': ['500'], 'page': ['9']}, 'jobs')
        self.assertEqual((empty['page'], empty['pages'], empty['total'], empty['jobs']), (1, 1, 0, []))

    def test_invalid_sizes_rejected(self):
        for value in ('', '0', '-1', '51', '501', '1000', '100.0', 'abc'):
            with self.assertRaises(ValueError):
                SharedAutomation.page(self.rows, {'pageSize': [value]}, 'jobs')

    def test_filters_and_counts_remain_independent_of_size_for_all_groups_and_queue(self):
        fixture = QueryFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        service = fixture.service
        fixture.item(1, 123)
        fixture.item(2, 123)
        fixture.item(2, 124, '22')
        data = []
        statuses = {'processing': 'queued', 'attention': 'failed', 'publish': 'ready_publish', 'history': 'done'}
        for group, status in statuses.items():
            for i in range(603):
                data.append((f'{group}:{i:04}', status, 123))
        data.append(('other', 'ready_publish', 124))
        with closing(fixture.pools[1].pools[0]._connect()) as db, db:
            db.executemany('INSERT INTO automation_jobs(job_id,detail_id,shop_id,site,status,step,message,created_at,updated_at) '
                'VALUES (?,123,7,\'US\',?,\'queued\',\'\',\'2026-10-21\',\'2026-10-21\')', [(key, status) for key, status, _ in data])
            db.executemany('INSERT INTO automation_job_media VALUES (?,\'商品\',\'\')', [(key,) for key, _, _ in data])
        with service.db() as db:
            db.executemany('INSERT INTO shared_job_origins VALUES (1,?,2,\'scope-2\',?,\'scope-1\')',
                           [(key, detail) for key, _, detail in data])
        for kind, group in [('jobs', g) for g in statuses] + [('queue', 'publish')]:
            for size in (100, 200, 500):
                result = service.listing(kind, {'taskGroup': [group], 'personKey': ['2:11'],
                                               'pageSize': [str(size)], 'page': ['2']})
                self.assertEqual(len(result['jobs']), min(size, 603 - size))
                self.assertEqual(result['total'], 603)
                self.assertEqual(result['counts'], dict.fromkeys(statuses, 603))
                self.assertEqual(result['shopPendingCounts'][0]['count'], 604)
                self.assertTrue(all(row['personKey'] == '2:11' for row in result['jobs']))


if __name__ == '__main__':
    unittest.main()
