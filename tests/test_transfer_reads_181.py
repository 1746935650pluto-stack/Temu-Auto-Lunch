"""Bulk copy lookup preserves review safety and account identity."""
from contextlib import contextmanager
import json
from unittest.mock import patch

import shared_automation
from test_task_queries import QueryFixture


class TransferReadTests(QueryFixture):
    @staticmethod
    def resolve(source, detail, scope):
        return {'personId': '11', 'personKey': f'{source}:11', 'personName': 'Owner'}

    def test_large_review_list_batches_copy_reads_and_decodes_payload_once(self):
        self.transfers(501)
        with self.service.db() as db:
            for i in range(501):
                db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (f'copy{i}', 1, 'scope-1', 1000+i, 2, 'scope-2', 5000+i,
                     'ready', '{"large":"must not load"}', ''))
        trace = []
        original_reader = shared_automation.reader
        @contextmanager
        def traced(*args, **kwargs):
            with original_reader(*args, **kwargs) as db:
                db.set_trace_callback(trace.append)
                yield db
        with patch.object(shared_automation, 'reader', traced), \
             patch.object(shared_automation.json, 'loads', wraps=json.loads) as loads:
            rows = self.service.transfer_rows(self.resolve)
        copy_queries = [sql for sql in trace if 'JOIN shared_copies' in sql]
        self.assertEqual(len(rows), 501)
        self.assertEqual(loads.call_count, 501)
        self.assertLessEqual(len(copy_queries), 4)
        self.assertTrue(all(row['canResume'] for row in rows))
        self.assertFalse(any('expected' in sql for sql in copy_queries))

    def test_copy_reservations_are_scope_qualified_and_unknown_result_stays_blocked(self):
        self.transfers(3)
        with self.service.db() as db:
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('ambiguous', 1, 'scope-1', 1000, 2, 'scope-2', None, 'needs_review', '{}', 'timeout'))
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('ready', 1, 'scope-1', 1001, 2, 'scope-2', 9001, 'ready', '{}', ''))
            # Same source ID under a replaced target credential is unrelated.
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('old', 1, 'scope-1', 1002, 2, 'old-target-scope', None, 'needs_review', '{}', 'timeout'))
        rows = {r['detailId']:r for r in self.service.transfer_rows(self.resolve)}
        self.assertFalse(rows[1000]['canResume'])
        self.assertFalse(rows[1000]['safeBatchEligible'])
        self.assertTrue(rows[1001]['canResume'])
        self.assertTrue(rows[1002]['canResume'])
        self.assertEqual(rows[1000]['payload']['sourceAccountId'], 1)
        self.assertNotIn('sourceScope', rows[1000]['payload'])

    def test_empty_review_list_does_not_query_copy_table(self):
        original_reader = shared_automation.reader
        trace = []
        @contextmanager
        def traced(*args, **kwargs):
            with original_reader(*args, **kwargs) as db:
                db.set_trace_callback(trace.append)
                yield db
        with patch.object(shared_automation, 'reader', traced):
            self.assertEqual(self.service.transfer_rows(self.resolve), [])
        self.assertFalse(any('shared_copies' in sql for sql in trace))
