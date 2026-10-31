"""Independent task counters must not load remote or product data."""
from unittest.mock import patch

from test_task_queries import QueryFixture
from task_queries import TaskQueries


class TaskSummaryTests(QueryFixture):
    def test_summary_matches_list_totals_across_accounts_and_hidden_records(self):
        for index, status in enumerate(('queued', 'running', 'needs_input', 'needs_review',
                                         'failed', 'interrupted', 'ready_publish', 'done', 'canceled')):
            self.job(1 + index % 2, index + 1, status)
        removed = self.job(2, 99, 'failed')
        self.pools[2].delete_job_records([removed])
        self.transfers(5)
        with self.service.db() as db:
            db.execute("UPDATE shared_transfers SET status='done' WHERE transfer_id='t0000'")
            db.execute("UPDATE shared_transfers SET status='discarded' WHERE transfer_id='t0001'")
            db.execute("UPDATE shared_transfers SET status='queued' WHERE transfer_id='t0002'")
        expected = self.service.listing('jobs', {})
        with (patch.object(self.service, 'transfer_rows', side_effect=AssertionError('payload hydration')),
              patch.object(self.people, 'list', side_effect=AssertionError('person metadata'))):
            actual = TaskQueries(self.service).summary()
        for key in ('counts', 'transferCounts', 'totalAll'):
            self.assertEqual(actual[key], expected[key])

    def test_summary_reads_beside_pool_writer_gate_and_empty_totals_are_zero(self):
        with patch.object(self.pools[1].pools[0], '_connect', side_effect=AssertionError('writer connection')):
            result = TaskQueries(self.service).summary()
        self.assertEqual(result['counts'], dict.fromkeys(('processing', 'attention', 'publish', 'history'), 0))
        self.assertEqual(result['transferCounts'], result['counts'])

    def test_http_summary_does_not_dispatch_remote_work(self):
        from types import SimpleNamespace
        calls = []
        handler = SimpleNamespace(_same_origin=lambda: True,
            _json=lambda status, body: calls.append((status, body)),
            _error=lambda *args: self.fail(str(args)))
        with patch.object(self.service, 'config', side_effect=AssertionError('remote config')):
            self.service.http(handler, 'GET', '/api/automation/shared/summary', {})
        self.assertEqual(calls[0][0], 200)
        self.assertEqual(calls[0][1]['data']['counts']['attention'], 0)
