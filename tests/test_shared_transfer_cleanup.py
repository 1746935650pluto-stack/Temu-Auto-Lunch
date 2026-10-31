"""Local-only cleanup: unknown remote outcomes must retain dedupe evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shared_automation import SharedAutomation, encoded
from test_task_queries import QueryFixture


class TransferCleanupTests(QueryFixture):
    def setUp(self):
        super().setUp()
        self.path = self.service.path
        self.backend.os = SimpleNamespace(getenv=lambda *_: 'live')
        self.service.job_rows = Mock(return_value=[])
        self.service.person_resolver = Mock(return_value=lambda *_: {'personId': '', 'personKey': 'unknown', 'personName': '未知归属'})
        self.transfer = dict(sourceAccountId=2, sourceScope='source', detailId=123,
                             targetAccountId=1, targetScope='target', shopIds=[7], site='US')
        self.key = hashlib.sha256(encoded(['source', 123, 'target']).encode()).hexdigest()

    def seed(self, message='Unable to reach MiaoShou API', status='needs_review', copy=True):
        with self.service.db() as db:
            db.execute('INSERT INTO shared_batches VALUES (?,?,?,?,?,?)', ('batch', '{}', '{}', 'submitted', 1, 8))
            db.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                       ('transfer', 'batch', json.dumps(self.transfer), status, message, None, 1))
            if copy:
                db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (self.key, 2, 'source', 123, 1, 'target', None, 'needs_review', '{}', 'different error'))

    def clear(self):
        return self.service.discard_transfer(dict(transferId='transfer', confirmation='DISCARD_SHARED_TRANSFER'))

    def test_all_errors_clear_without_changing_copy_or_audit_data(self):
        self.seed('英文标题逗号后面需要添加空格')
        before = self.service._copies()
        self.assertTrue(self.clear()['discarded'])
        self.assertEqual(self.service._copies(), before)
        with self.service.db() as db:
            row = db.execute('SELECT * FROM shared_transfers').fetchone()
            self.assertEqual(row['status'], 'discarded')
            self.assertEqual(row['message'], '英文标题逗号后面需要添加空格')
            self.assertEqual(json.loads(row['payload']), self.transfer)

    def test_hidden_from_all_listings_and_counts_after_restart(self):
        self.seed()
        self.assertEqual(self.service.listing('jobs', {})['transferCounts']['attention'], 1)
        self.clear()
        self.service = SharedAutomation(self.backend, self.path)
        self.service.accounts = lambda: self.accounts
        self.service.recover()
        self.service.job_rows = Mock(return_value=[])
        self.service.person_resolver = Mock(return_value=lambda *_: {'personId': '', 'personKey': 'unknown', 'personName': '未知归属'})
        for kind, query in [('jobs', {}), ('jobs', {'taskGroup':['attention']}),
                            ('jobs', {'status':['discarded']}), ('queue', {})]:
            data = self.service.listing(kind, query)
            self.assertEqual(data['transfers'], [])
            self.assertEqual(sum(data['transferCounts'].values()), 0)
        self.assertEqual(len(self.service._copies()), 1)

    def test_duplicate_clear_is_idempotent_and_missing_copy_is_safe(self):
        self.seed(copy=False)
        self.assertEqual(self.clear(), self.clear())

    def test_running_queued_and_finished_cannot_be_cleared(self):
        self.seed()
        for status in ['running', 'queued', 'done']:
            with self.service.db() as db:
                db.execute('UPDATE shared_transfers SET status=?', (status,))
            with self.assertRaises(ValueError):
                self.clear()

    def test_confirmation_and_id_are_required(self):
        self.seed()
        for body in [{}, {'transferId':'transfer'},
                     {'transferId':[], 'confirmation':'DISCARD_SHARED_TRANSFER'},
                     {'transferId':'missing', 'confirmation':'DISCARD_SHARED_TRANSFER'}]:
            with self.assertRaises(ValueError):
                self.service.discard_transfer(body)

    def test_unknown_copy_still_blocks_creation_after_clear(self):
        self.seed()
        self.clear()
        self.service.check_account = Mock()
        self.service.worker = Mock(side_effect=AssertionError('No remote request allowed'))
        with self.assertRaisesRegex(ValueError, '不会自动再次创建'):
            self.service._ensure_copy(self.transfer)
        self.service.worker.assert_not_called()

    def test_stale_reconcile_cannot_restart_cleared_record(self):
        self.seed()
        self.clear()
        with self.assertRaisesRegex(ValueError, '仅允许核查'):
            self.service.reconcile(dict(transferId='transfer', confirmation='RECONCILE_SHARED_COPY'))

    def test_clear_serializes_with_reconcile(self):
        self.seed()
        entered, release, cleared = Event(), Event(), Event()
        def reconcile(_):
            entered.set()
            self.assertTrue(release.wait(3))
        def clear():
            self.clear()
            cleared.set()
        with patch.object(self.service, '_reconcile', side_effect=reconcile):
            first = Thread(target=self.service.reconcile, args=({},))
            second = Thread(target=clear)
            first.start()
            try:
                self.assertTrue(entered.wait(3))
                second.start()
                self.assertFalse(cleared.wait(.05))
            finally:
                release.set()
                first.join(3)
                if second.ident is not None:
                    second.join(3)
            self.assertTrue(cleared.is_set())


if __name__ == '__main__':
    unittest.main()
