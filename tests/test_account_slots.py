import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from account_slots import AccountSlotStore


class AccountSlotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'accounts.sqlite3'
        self.store = AccountSlotStore(self.path)
        self.configured = [{'id': 1, 'name': '默认', 'active': True, 'configured': True, 'deletable': False},
                           {'id': 2, 'name': '妙手采集箱 2', 'active': False, 'configured': False, 'deletable': True}]

    def test_deleted_default_slot_stays_deleted_after_restart_and_can_be_readded(self):
        self.assertEqual([s['id'] for s in self.store.delete(2, self.configured)], [1])
        restarted = AccountSlotStore(self.path)
        self.assertEqual([s['id'] for s in restarted.list(self.configured)], [1])
        slot_id, slots = restarted.add(self.configured)
        self.assertEqual(slot_id, 2)
        self.assertEqual([s['id'] for s in AccountSlotStore(self.path).list(self.configured)], [1, 2])

    def test_new_credentials_override_prior_deletion_and_prevent_reuse(self):
        self.store.delete(2, self.configured)
        self.configured[1]['deletable'] = False
        self.assertEqual([s['id'] for s in self.store.list(self.configured)], [1, 2])
        with self.assertRaisesRegex(ValueError, '只能删除'):
            self.store.delete(2, self.configured)
        self.assertEqual(self.store.add(self.configured)[0], 3)

    def test_concurrent_adds_allocate_unique_slots_and_obey_limit(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            ids = list(workers.map(lambda _: self.store.add(self.configured, limit=6)[0], range(4)))
        self.assertEqual(sorted(ids), [3, 4, 5, 6])
        with self.assertRaisesRegex(ValueError, '最多支持 6'):
            self.store.add(self.configured, limit=6)
        self.assertEqual(len(self.store.list(self.configured)), 6)

    def test_invalid_or_primary_deletion_does_not_change_anything(self):
        for slot in (1, 0, -1, 999, True, '2'):
            with self.assertRaises(ValueError):
                self.store.delete(slot, self.configured)
        self.assertEqual(self.store.list(self.configured), self.configured)
