from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from automation_pool import AutomationPool
from partitioned_pool import PartitionedPool


class SnapshotRoutingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.pool = PartitionedPool(*(AutomationPool(root / f'pool-{i}.sqlite3') for i in (1, 2, 3)), 1)

    def missed(self, detail, partition=1):
        pool = self.pool.pools[partition - 1]
        pool.put_item(detail, 'site', 0, {'title': f'item-{detail}'})
        job = pool.queue_jobs([detail], 7, 'US')[0]
        pool.pause_for_problem(job['jobId'], 'claim', '商品已从自动化池移除')
        return pool, job['jobId']

    def test_stale_route_falls_back_to_durable_snapshot_and_repairs_future_writes(self):
        target = self.pool.pools[2]
        target.put_item(101, 'site', 0, {'title': 'still present'})
        self.pool._items[101] = self.pool.pools[0]
        self.assertEqual(self.pool.get_item(101)['poolId'], 3)
        self.pool.put_item(101, 'site', 0, {'title': 'updated'})
        self.assertEqual(target.get_item(101)['title'], 'updated')
        self.assertEqual(sum(101 in p.item_ids() for p in self.pool.pools), 1)

    def test_second_router_can_move_an_unused_item_without_false_deletion(self):
        first = self.pool.pools[0]
        first.put_item(101, 'site', 0, {'title': 'move me'})
        self.assertEqual(self.pool.get_item(101)['poolId'], 1)
        for detail in (201, 202):
            first.put_item(detail, 'site', 0, {'title': 'busy'})
            first.queue_jobs([detail], 7, 'US')
        second = PartitionedPool(*(AutomationPool(p.path) for p in self.pool.pools), 1)
        moved = second.queue_jobs([101], 8, 'US')[0]
        self.assertIn(moved['poolId'], (2, 3))
        self.assertEqual(self.pool.get_item(101)['poolId'], moved['poolId'])

    def test_real_missing_snapshot_returns_none_without_creating_data(self):
        self.pool._items[101] = self.pool.pools[2]
        self.assertIsNone(self.pool.get_item(101))
        self.assertEqual(self.pool.item_ids(), set())

    def test_only_proven_false_misses_requeue_with_same_identity_and_no_publish(self):
        jobs = [self.missed(100 + p, p)[1] for p in (1, 2, 3)]
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 3)
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 0)
        self.assertEqual({j['jobId'] for j in self.pool.list_jobs()}, set(jobs))
        for job in jobs:
            row = self.pool.get_job(job)
            self.assertEqual(row['status'], 'queued')
            self.assertFalse(row['publishAfterSave'])
            self.assertTrue(any(e['message'] == '商品已从自动化池移除' for e in row['events']))

    def test_remote_attempts_missing_snapshots_and_missing_audit_are_not_replayed(self):
        for detail, reason in ((101, 'claim'), (102, 'save'), (103, 'missing'), (104, 'audit')):
            pool, job = self.missed(detail)
            if reason in ('claim', 'save'):
                pool.update_job(job, reason, 'running', 'remote operation could have started')
                pool.pause_for_problem(job, 'claim', '商品已从自动化池移除')
            else:
                with closing(pool._connect()) as db, db:
                    if reason == 'missing':
                        db.execute('DELETE FROM automation_items WHERE detail_id=?', (detail,))
                    else:
                        db.execute('DELETE FROM automation_job_events WHERE job_id=?', (job,))
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 0)
        self.assertTrue(all(j['status'] == 'needs_input' for j in self.pool.list_jobs()))

    def test_hidden_jobs_and_publication_membership_are_not_recovered(self):
        pool, hidden = self.missed(101)
        # Legacy hidden records can retain their old paused status. Current
        # deletion correctly requires cancellation first; recovery must honor
        # the durable tombstone regardless of that legacy status.
        with closing(pool._connect()) as db, db:
            db.execute("INSERT INTO automation_job_deletions(job_id,deleted_at) VALUES (?,?)",
                       (hidden, '2026-10-28T00:00:00+00:00'))
        pool, released = self.missed(102)
        pool.update_job(released, 'queued', 'ready_publish', 'released for publication')
        pool.begin_publish_batch([released], 1)
        pool.pause_for_problem(released, 'claim', '商品已从自动化池移除')
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 0)

    def test_same_pair_in_another_pool_blocks_recovery_even_when_hidden(self):
        pool, failed = self.missed(101)
        other = self.pool.pools[2]
        other.put_item(101, 'site', 0, {'title': 'historical duplicate'})
        duplicate = other.queue_jobs([101], 7, 'US')[0]
        other.update_job(duplicate['jobId'], 'complete', 'done', 'previously accepted')
        other.delete_job_record(duplicate['jobId'])
        with closing(other._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET site='us' WHERE job_id=?", (duplicate['jobId'],))
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 0)
        self.assertEqual(pool.get_job(failed)['status'], 'needs_input')

    def test_other_shop_and_site_do_not_block_recovery(self):
        pool, failed = self.missed(101)
        other = self.pool.pools[1]
        other.put_item(101, 'site', 0, {'title': 'independent site'})
        other.queue_jobs([101], 7, 'CA')
        other.queue_jobs([101], 8, 'US', dedupe_pairs=True)
        self.assertEqual(self.pool.requeue_missing_snapshot_jobs(), 1)
        self.assertEqual(pool.get_job(failed)['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
